"""PowerMesh environment diagnostics."""

from __future__ import annotations

import importlib
import socket
import sys
from pathlib import Path

from .config import load_mesh_config, load_node_config
from .platform_detect import detect_platform


def main() -> None:
    checks = [
        _check_python(),
        _check_import("psutil"),
        _check_import("requests"),
        _check_import("yaml"),
        _check_config(),
        _check_port(),
        _check_platform(),
    ]
    for ok, label, detail in checks:
        icon = "OK" if ok else "FAIL"
        print(f"[{icon}] {label}: {detail}")
    raise SystemExit(0 if all(ok for ok, _, _ in checks) else 1)


def _check_python() -> tuple[bool, str, str]:
    ok = sys.version_info >= (3, 10)
    return ok, "Python", sys.version.split()[0]


def _check_import(module: str) -> tuple[bool, str, str]:
    try:
        importlib.import_module(module)
        return True, f"Import {module}", "available"
    except ImportError as exc:
        return False, f"Import {module}", str(exc)


def _check_config() -> tuple[bool, str, str]:
    mesh = load_mesh_config()
    node = load_node_config()
    for cfg in (mesh, node):
        data_dir = Path(cfg["data_dir"])
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    return True, "Config/data dirs", "writable"


def _check_port() -> tuple[bool, str, str]:
    port = load_mesh_config().get("port", 8430)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        result = sock.connect_ex(("127.0.0.1", int(port)))
    if result == 0:
        return True, "Collector port", f"127.0.0.1:{port} already in use (collector may be running)"
    return True, "Collector port", f"127.0.0.1:{port} available"


def _check_platform() -> tuple[bool, str, str]:
    platform = detect_platform()
    sensors = []
    if platform.has_nvidia_smi:
        sensors.append("nvidia-smi")
    if platform.has_rapl:
        sensors.append("RAPL")
    if not sensors:
        sensors.append("TDP estimation")
    return True, "Platform", f"{platform.os}, {platform.cpu_cores} threads, sensors: {', '.join(sensors)}"


if __name__ == "__main__":
    main()
