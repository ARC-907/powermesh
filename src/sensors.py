"""Cross-platform sensor abstraction for power, thermal, and utilization data."""

# pylint: disable=broad-exception-caught

from __future__ import annotations

import importlib.util
import logging
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

from .platform_detect import PlatformInfo

log = logging.getLogger("powermesh.sensors")


# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class GpuReading:
    index: int = 0
    name: str = ""
    power_w: float = 0.0
    temperature_c: float = 0.0
    utilization_pct: float = 0.0
    vram_used_mb: float = 0.0
    vram_total_mb: float = 0.0


@dataclass
class CpuReading:
    power_w: float = 0.0
    utilization_pct: float = 0.0
    method: str = "unknown"  # "rapl" | "tdp_estimate"


@dataclass
class SystemReading:
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    disk_io_read_mb: float = 0.0
    disk_io_write_mb: float = 0.0
    net_sent_mb: float = 0.0
    net_recv_mb: float = 0.0


@dataclass
class SmartPlugReading:
    power_w: float = 0.0
    voltage_v: float = 0.0
    current_a: float = 0.0
    available: bool = False


@dataclass
class PowerSnapshot:
    """Complete reading from all sensors at a point in time."""
    cpu: CpuReading = field(default_factory=CpuReading)
    gpus: list[GpuReading] = field(default_factory=list)
    system: SystemReading = field(default_factory=SystemReading)
    smart_plug: SmartPlugReading = field(default_factory=SmartPlugReading)

    @property
    def total_gpu_power_w(self) -> float:
        return sum(g.power_w for g in self.gpus)

    @property
    def avg_gpu_util(self) -> float:
        if not self.gpus:
            return 0.0
        return sum(g.utilization_pct for g in self.gpus) / len(self.gpus)

    @property
    def avg_gpu_temp(self) -> float:
        if not self.gpus:
            return 0.0
        return sum(g.temperature_c for g in self.gpus) / len(self.gpus)

    @property
    def total_vram_used_mb(self) -> float:
        return sum(g.vram_used_mb for g in self.gpus)


# ── Base sensor ──────────────────────────────────────────────────────

class Sensor(ABC):
    @abstractmethod
    def read(self) -> Any:
        ...

    @abstractmethod
    def available(self) -> bool:
        ...


# ── NVIDIA GPU sensor ────────────────────────────────────────────────

class NvidiaSmiSensor(Sensor):
    """Read GPU power/temp/util/vram via nvidia-smi."""

    def __init__(self, smi_path: str = "nvidia-smi") -> None:
        self._smi = smi_path

    def available(self) -> bool:
        try:
            r = subprocess.run(
                [self._smi, "--query-gpu=count", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            return r.returncode == 0
        except Exception:
            return False

    def read(self) -> list[GpuReading]:
        try:
            result = subprocess.run(
                [
                    self._smi,
                    "--query-gpu=index,name,power.draw,temperature.gpu,"
                    "utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if result.returncode != 0:
                log.warning("nvidia-smi returned %d: %s", result.returncode, result.stderr)
                return []

            readings = []
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 7:
                    continue
                readings.append(GpuReading(
                    index=int(parts[0]),
                    name=parts[1],
                    power_w=_safe_float(parts[2]),
                    temperature_c=_safe_float(parts[3]),
                    utilization_pct=_safe_float(parts[4]),
                    vram_used_mb=_safe_float(parts[5]),
                    vram_total_mb=_safe_float(parts[6]),
                ))
            return readings
        except Exception as e:
            log.error("nvidia-smi read failed: %s", e)
            return []


# ── RAPL CPU power sensor (Linux) ────────────────────────────────────

class RaplSensor(Sensor):
    """Read CPU package power via Linux RAPL sysfs interface."""

    _RAPL_BASE = Path("/sys/class/powercap")

    def __init__(self) -> None:
        self._last_energy: dict[str, int] = {}
        self._last_time: float = 0
        self._domain_paths: dict[str, Path] = {}
        self._discover_domains()

    def _discover_domains(self) -> None:
        for prefix in ("intel-rapl", "amd-rapl"):
            base = self._RAPL_BASE / prefix
            if not base.exists():
                continue
            for domain_dir in sorted(base.glob(f"{prefix}:*")):
                energy_file = domain_dir / "energy_uj"
                name_file = domain_dir / "name"
                if energy_file.exists() and name_file.exists():
                    try:
                        name = name_file.read_text().strip()
                        self._domain_paths[name] = energy_file
                    except PermissionError:
                        pass

    def available(self) -> bool:
        return len(self._domain_paths) > 0

    def read(self) -> CpuReading:
        now = time.monotonic()
        current_energy: dict[str, int] = {}

        for name, path in self._domain_paths.items():
            try:
                current_energy[name] = int(path.read_text().strip())
            except (PermissionError, ValueError):
                pass

        if not self._last_energy or not self._last_time:
            self._last_energy = current_energy
            self._last_time = now
            cpu_util = psutil.cpu_percent(interval=0)
            return CpuReading(power_w=0, utilization_pct=cpu_util, method="rapl")

        dt = now - self._last_time
        if dt <= 0:
            return CpuReading(method="rapl")

        total_power = 0.0
        for name in current_energy:
            if name in self._last_energy:
                delta_uj = current_energy[name] - self._last_energy[name]
                if delta_uj < 0:
                    # Counter wrapped
                    delta_uj += 2**32
                power_w = (delta_uj / 1e6) / dt  # µJ → J, then J / s = W
                total_power += power_w

        self._last_energy = current_energy
        self._last_time = now
        cpu_util = psutil.cpu_percent(interval=0)

        return CpuReading(
            power_w=round(total_power, 2),
            utilization_pct=cpu_util,
            method="rapl",
        )


# ── TDP estimation sensor (fallback) ────────────────────────────────

class TdpEstimationSensor(Sensor):
    """Estimate CPU power from utilization × TDP. Fallback when RAPL unavailable."""

    def __init__(self, cpu_tdp_w: float = 65, idle_fraction: float = 0.15) -> None:
        self._tdp = cpu_tdp_w
        self._idle_fraction = idle_fraction  # CPU draws ~15% of TDP at idle

    def available(self) -> bool:
        return True  # Always available as fallback

    def read(self) -> CpuReading:
        util = psutil.cpu_percent(interval=0)
        idle_power = self._tdp * self._idle_fraction
        dynamic_power = self._tdp * (1 - self._idle_fraction) * (util / 100.0)
        estimated_w = idle_power + dynamic_power
        return CpuReading(
            power_w=round(estimated_w, 2),
            utilization_pct=util,
            method="tdp_estimate",
        )


# ── System metrics sensor ────────────────────────────────────────────

class SystemSensor(Sensor):
    """Read RAM, disk I/O, and network I/O via psutil."""

    def __init__(self) -> None:
        self._last_disk = psutil.disk_io_counters()
        self._last_net = psutil.net_io_counters()

    def available(self) -> bool:
        return True

    def read(self) -> SystemReading:
        mem = psutil.virtual_memory()
        disk = psutil.disk_io_counters()
        net = psutil.net_io_counters()

        disk_read_mb = 0.0
        disk_write_mb = 0.0
        if self._last_disk and disk:
            disk_read_mb = (disk.read_bytes - self._last_disk.read_bytes) / (1024**2)
            disk_write_mb = (disk.write_bytes - self._last_disk.write_bytes) / (1024**2)

        net_sent_mb = 0.0
        net_recv_mb = 0.0
        if self._last_net and net:
            net_sent_mb = (net.bytes_sent - self._last_net.bytes_sent) / (1024**2)
            net_recv_mb = (net.bytes_recv - self._last_net.bytes_recv) / (1024**2)

        self._last_disk = disk
        self._last_net = net

        return SystemReading(
            ram_used_gb=round(mem.used / (1024**3), 2),
            ram_total_gb=round(mem.total / (1024**3), 2),
            disk_io_read_mb=round(max(0, disk_read_mb), 2),
            disk_io_write_mb=round(max(0, disk_write_mb), 2),
            net_sent_mb=round(max(0, net_sent_mb), 2),
            net_recv_mb=round(max(0, net_recv_mb), 2),
        )


# ── Smart plug sensor (optional) ────────────────────────────────────

class SmartPlugSensor(Sensor):
    """Read wall power from a TP-Link Kasa or Shelly smart plug."""

    def __init__(self, plug_type: str = "kasa", plug_ip: str = "") -> None:
        self._type = plug_type
        self._ip = plug_ip

    def available(self) -> bool:
        if not self._ip:
            return False
        if self._type == "kasa":
            return importlib.util.find_spec("kasa") is not None
        if self._type == "shelly":
            return True  # Uses requests, always available
        return False

    def read(self) -> SmartPlugReading:
        if self._type == "kasa":
            return self._read_kasa()
        if self._type == "shelly":
            return self._read_shelly()
        return SmartPlugReading()

    def _read_kasa(self) -> SmartPlugReading:
        try:
            import asyncio
            from kasa import SmartPlug  # type: ignore[import-not-found]

            async def _fetch():
                plug = SmartPlug(self._ip)
                await plug.update()
                emeter = plug.emeter_realtime
                return SmartPlugReading(
                    power_w=emeter.get("power_mw", emeter.get("power", 0) * 1000) / 1000,
                    voltage_v=emeter.get("voltage_mv", emeter.get("voltage", 0) * 1000) / 1000,
                    current_a=emeter.get("current_ma", emeter.get("current", 0) * 1000) / 1000,
                    available=True,
                )

            return asyncio.run(_fetch())
        except Exception as e:
            log.warning("Kasa read failed (%s): %s", self._ip, e)
            return SmartPlugReading()

    def _read_shelly(self) -> SmartPlugReading:
        try:
            import requests
            resp = requests.get(
                f"http://{self._ip}/rpc/Switch.GetStatus?id=0", timeout=5
            )
            data = resp.json()
            return SmartPlugReading(
                power_w=data.get("apower", 0),
                voltage_v=data.get("voltage", 0),
                current_a=data.get("current", 0),
                available=True,
            )
        except Exception as e:
            log.warning("Shelly read failed (%s): %s", self._ip, e)
            return SmartPlugReading()


# ── PSU efficiency model ─────────────────────────────────────────────

# 80 Plus Bronze typical efficiency curve
_PSU_CURVE: dict[str, list[tuple[float, float]]] = {
    "bronze": [(0.10, 0.81), (0.20, 0.85), (0.50, 0.88), (1.00, 0.85)],
    "silver": [(0.10, 0.82), (0.20, 0.87), (0.50, 0.90), (1.00, 0.87)],
    "gold":   [(0.10, 0.84), (0.20, 0.89), (0.50, 0.92), (1.00, 0.89)],
    "platinum":[(0.10, 0.86), (0.20, 0.91), (0.50, 0.94), (1.00, 0.91)],
    "titanium":[(0.10, 0.90), (0.20, 0.94), (0.50, 0.96), (1.00, 0.91)],
}


def estimate_psu_efficiency(
    component_power_w: float,
    psu_wattage: float = 650,
    psu_rating: str = "bronze",
) -> float:
    """Interpolate PSU efficiency from load percentage and rating curve."""
    if psu_wattage <= 0:
        return 0.85
    load_frac = min(component_power_w / psu_wattage, 1.0)
    curve = _PSU_CURVE.get(psu_rating.lower(), _PSU_CURVE["bronze"])

    if load_frac <= curve[0][0]:
        return curve[0][1]
    if load_frac >= curve[-1][0]:
        return curve[-1][1]

    for i in range(len(curve) - 1):
        x0, y0 = curve[i]
        x1, y1 = curve[i + 1]
        if x0 <= load_frac <= x1:
            t = (load_frac - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)

    return 0.85


# ── Composite sensor orchestrator ────────────────────────────────────

class SensorManager:
    """Orchestrates all sensors and produces a complete PowerSnapshot."""

    def __init__(self, platform_info: PlatformInfo, config: dict[str, Any]) -> None:
        self.platform = platform_info
        self.config = config
        self._sensors: dict[str, Sensor] = {}
        self._init_sensors()

    def _init_sensors(self) -> None:
        # GPU
        if self.platform.has_nvidia_smi:
            self._sensors["gpu"] = NvidiaSmiSensor(self.platform.nvidia_smi_path)

        # CPU power
        if self.platform.has_rapl:
            rapl = RaplSensor()
            if rapl.available():
                self._sensors["cpu"] = rapl
        if "cpu" not in self._sensors:
            tdp = self.config.get("cpu_tdp_w", 65)
            self._sensors["cpu"] = TdpEstimationSensor(cpu_tdp_w=tdp)

        # System
        self._sensors["system"] = SystemSensor()

        # Smart plug
        plug_cfg = self.config.get("smart_plug", {})
        if plug_cfg.get("enabled"):
            plug = SmartPlugSensor(
                plug_type=plug_cfg.get("type", "kasa"),
                plug_ip=plug_cfg.get("ip", ""),
            )
            if plug.available():
                self._sensors["smart_plug"] = plug

        log.info(
            "Sensors initialized: %s",
            ", ".join(self._sensors.keys()),
        )

    def collect(self) -> PowerSnapshot:
        """Collect a complete snapshot from all available sensors."""
        snapshot = PowerSnapshot()

        # CPU
        cpu_sensor = self._sensors.get("cpu")
        if cpu_sensor:
            snapshot.cpu = cpu_sensor.read()

        # GPU
        gpu_sensor = self._sensors.get("gpu")
        if gpu_sensor:
            snapshot.gpus = gpu_sensor.read()

        # System
        sys_sensor = self._sensors.get("system")
        if sys_sensor:
            snapshot.system = sys_sensor.read()

        # Smart plug
        plug_sensor = self._sensors.get("smart_plug")
        if plug_sensor:
            snapshot.smart_plug = plug_sensor.read()

        return snapshot


# ── Helpers ──────────────────────────────────────────────────────────

def _safe_float(val: str) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0
