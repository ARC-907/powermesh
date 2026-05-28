"""Path helpers for PowerMesh runtime, config, reports, and tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "PowerMesh"


def project_root() -> Path:
    """Return the source checkout or installed application root."""
    return Path(__file__).resolve().parent.parent


def expand_path(value: str | Path, base: str | Path | None = None) -> Path:
    """Expand env vars, `~`, and relative paths from a stable base."""
    raw = Path(os.path.expandvars(os.path.expanduser(str(value))))
    if raw.is_absolute():
        return raw
    return Path(base or Path.cwd()) / raw


def user_config_dir() -> Path:
    """Return the per-user config directory without requiring platformdirs."""
    override = os.environ.get("POWERMESH_CONFIG_DIR")
    if override:
        return expand_path(override)
    if os.name == "nt":
        root = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        root = str(Path.home() / "Library" / "Application Support")
    else:
        root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(root) / APP_NAME


def user_data_dir() -> Path:
    """Return the per-user writable data directory."""
    override = os.environ.get("POWERMESH_DATA_HOME")
    if override:
        return expand_path(override)
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = str(Path.home() / "Library" / "Application Support")
    else:
        root = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(root) / APP_NAME


def default_config_path(filename: str) -> Path:
    return project_root() / "config" / filename


def reports_dir() -> Path:
    return user_data_dir() / "reports"


def ensure_dir(path: str | Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory
