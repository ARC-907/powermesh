"""Configuration loading and environment override helpers."""

from __future__ import annotations

import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import yaml

from .paths import default_config_path, expand_path, user_config_dir, user_data_dir

log = logging.getLogger("powermesh.config")

DEFAULT_NODE_CONFIG: dict[str, Any] = {
    "node_id": "",
    "collector_url": "",
    "auth_token": "",
    "cpu_tdp_w": 65,
    "gpu_tdp_w": 250,
    "base_power_w": 35,
    "psu_wattage": 650,
    "psu_rating": "bronze",
    "cost_per_kwh": 0.12,
    "currency": "USD",
    "collection_interval_s": 60,
    "push_batch_size": 10,
    "smart_plug": {"enabled": False, "type": "kasa", "ip": ""},
    "data_dir": "data",
    "log_level": "INFO",
}

DEFAULT_MESH_CONFIG: dict[str, Any] = {
    "port": 8430,
    # Loopback by default. To expose on LAN: set host explicitly AND
    # populate auth_tokens, then start the collector with --public.
    "host": "127.0.0.1",
    "data_dir": "data",
    "auth_tokens": {},
    "expected_nodes": [],
    "aggregation_interval_m": 60,
    "retention_days": 30,
    "cost_per_kwh_default": 0.12,
    "log_level": "INFO",
    # Explicit allowlist of origins permitted to make cross-origin requests
    # against the API. Empty list = same-origin only (no Access-Control-Allow-Origin
    # header emitted). The dashboard is same-origin and does not need CORS.
    "cors_allow_origins": [],
}

EnvMap = dict[str, tuple[str, Callable[[str], Any]]]

NODE_ENV: EnvMap = {
    "POWERMESH_NODE_ID": ("node_id", str),
    "POWERMESH_COLLECTOR_URL": ("collector_url", str),
    "POWERMESH_AUTH_TOKEN": ("auth_token", str),
    "POWERMESH_DATA_DIR": ("data_dir", str),
    "POWERMESH_LOG_LEVEL": ("log_level", str),
    "POWERMESH_COLLECTION_INTERVAL_S": ("collection_interval_s", int),
    "POWERMESH_PUSH_BATCH_SIZE": ("push_batch_size", int),
}

MESH_ENV: EnvMap = {
    "POWERMESH_COLLECTOR_PORT": ("port", int),
    "POWERMESH_HOST": ("host", str),
    "POWERMESH_DATA_DIR": ("data_dir", str),
    "POWERMESH_LOG_LEVEL": ("log_level", str),
    "POWERMESH_RETENTION_DAYS": ("retention_days", int),
    "POWERMESH_COST_PER_KWH": ("cost_per_kwh_default", float),
}

SECRET_KEYS = {"auth_token", "auth_tokens", "token", "password", "secret"}


def load_node_config(config_path: str | Path | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    return _load_config(
        defaults=DEFAULT_NODE_CONFIG,
        env_map=NODE_ENV,
        filename="node.yaml",
        config_path=config_path,
        explicit_config=config,
        data_subdir="agent",
    )


def load_mesh_config(config_path: str | Path | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    mesh_config = _load_config(
        defaults=DEFAULT_MESH_CONFIG,
        env_map=MESH_ENV,
        filename="mesh.yaml",
        config_path=config_path,
        explicit_config=config,
        data_subdir="collector",
    )
    if mesh_config.get("expected_nodes") is None:
        mesh_config["expected_nodes"] = []
    if mesh_config.get("auth_tokens") is None:
        mesh_config["auth_tokens"] = {}
    return mesh_config


def save_user_config(filename: str, config: dict[str, Any]) -> Path:
    target = user_config_dir() / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in config.items() if not k.startswith("_")}
    target.write_text(yaml.safe_dump(clean, sort_keys=False), encoding="utf-8")
    return target


def masked_config(config: dict[str, Any]) -> dict[str, Any]:
    masked: dict[str, Any] = {}
    for key, value in config.items():
        if key.startswith("_"):
            continue
        if key.lower() in SECRET_KEYS:
            masked[key] = _mask_value(value)
        elif isinstance(value, dict):
            masked[key] = masked_config(value)
        else:
            masked[key] = value
    return masked


def _load_config(
    defaults: dict[str, Any],
    env_map: EnvMap,
    filename: str,
    config_path: str | Path | None,
    explicit_config: dict[str, Any] | None,
    data_subdir: str,
) -> dict[str, Any]:
    config = deepcopy(defaults)
    sources: list[str] = []

    if explicit_config:
        _deep_update(config, explicit_config)
        sources.append("inline")
    else:
        for path in _candidate_paths(filename, config_path):
            if path.exists():
                with open(path, encoding="utf-8") as handle:
                    data = yaml.safe_load(handle) or {}
                _deep_update(config, data)
                sources.append(str(path))

    _apply_env(config, env_map)
    _normalize_data_dir(config, sources, data_subdir)
    config["_sources"] = sources
    return config


def _candidate_paths(filename: str, config_path: str | Path | None) -> list[Path]:
    if config_path:
        return [expand_path(config_path)]
    return [default_config_path(filename), user_config_dir() / filename]


def _apply_env(config: dict[str, Any], env_map: EnvMap) -> None:
    for env_name, (key, caster) in env_map.items():
        if env_name not in os.environ:
            continue
        value = os.environ[env_name]
        if value == "":
            continue
        config[key] = caster(value)


def _normalize_data_dir(config: dict[str, Any], sources: list[str], data_subdir: str) -> None:
    raw = config.get("data_dir") or str(user_data_dir() / data_subdir)
    raw_path = Path(str(raw))
    if raw_path.is_absolute():
        config["data_dir"] = str(raw_path)
        return

    if sources and sources[-1] != "inline":
        source_path = Path(sources[-1])
        if source_path.parent == user_config_dir():
            base = user_data_dir()
        elif source_path.parent.name == "config":
            base = source_path.parent.parent
        else:
            base = source_path.parent
    else:
        base = Path.cwd()
    config["data_dir"] = str(expand_path(raw_path, base=base))


def _deep_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _mask_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("********" if item else "") for key, item in value.items()}
    if value:
        return "********"
    return ""
