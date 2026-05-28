"""Platform detection — enumerate OS, available sensors, and hardware capabilities."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GpuInfo:
    index: int
    name: str
    vendor: str  # "nvidia" | "amd" | "intel"
    vram_mb: float = 0
    tdp_w: float = 0


@dataclass
class PlatformInfo:
    os: str  # "windows" | "linux" | "darwin"
    hostname: str = ""
    cpu_name: str = ""
    cpu_cores: int = 0
    ram_total_gb: float = 0

    has_nvidia_smi: bool = False
    has_rapl: bool = False
    has_amd_smi: bool = False
    has_rocm_smi: bool = False

    nvidia_smi_path: str = ""
    gpus: list[GpuInfo] = field(default_factory=list)

    rapl_domains: list[str] = field(default_factory=list)


def detect_platform() -> PlatformInfo:
    """Auto-detect OS, available sensors, and hardware."""
    import psutil

    info = PlatformInfo(
        os=platform.system().lower(),
        hostname=platform.node(),
        cpu_name=platform.processor() or _get_cpu_name(),
        cpu_cores=psutil.cpu_count(logical=True) or 0,
        ram_total_gb=round(psutil.virtual_memory().total / (1024**3), 1),
    )

    _detect_nvidia(info)
    _detect_rapl(info)
    _detect_amd(info)

    return info


def _get_cpu_name() -> str:
    try:
        if platform.system() == "Windows":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            )
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            winreg.CloseKey(key)
            return name.strip()
        else:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "Unknown"


def _detect_nvidia(info: PlatformInfo) -> None:
    smi = shutil.which("nvidia-smi")
    if not smi:
        if info.os == "windows":
            candidate = Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"
            if candidate.exists():
                smi = str(candidate)
    if not smi:
        return

    info.has_nvidia_smi = True
    info.nvidia_smi_path = smi

    try:
        result = subprocess.run(
            [smi, "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    info.gpus.append(GpuInfo(
                        index=int(parts[0]),
                        name=parts[1],
                        vendor="nvidia",
                        vram_mb=float(parts[2]),
                    ))
    except Exception:
        pass


def _detect_rapl(info: PlatformInfo) -> None:
    if info.os != "linux":
        return
    rapl_base = Path("/sys/class/powercap/intel-rapl")
    if not rapl_base.exists():
        # Try AMD RAPL path
        rapl_base = Path("/sys/class/powercap/amd-rapl")
    if not rapl_base.exists():
        return

    info.has_rapl = True
    for domain_dir in sorted(rapl_base.glob("intel-rapl:*")):
        name_file = domain_dir / "name"
        energy_file = domain_dir / "energy_uj"
        if name_file.exists() and energy_file.exists():
            try:
                name = name_file.read_text().strip()
                info.rapl_domains.append(name)
            except PermissionError:
                pass

    # Also check AMD sub-path naming
    for domain_dir in sorted(rapl_base.glob("amd-rapl:*")):
        name_file = domain_dir / "name"
        if name_file.exists():
            try:
                info.rapl_domains.append(name_file.read_text().strip())
            except PermissionError:
                pass


def _detect_amd(info: PlatformInfo) -> None:
    if shutil.which("rocm-smi"):
        info.has_rocm_smi = True
    if shutil.which("amd-smi"):
        info.has_amd_smi = True


def print_platform_report(info: PlatformInfo | None = None) -> None:
    """Print a human-readable platform capability report."""
    if info is None:
        info = detect_platform()

    print(f"  OS:           {info.os}")
    print(f"  Hostname:     {info.hostname}")
    print(f"  CPU:          {info.cpu_name} ({info.cpu_cores} threads)")
    print(f"  RAM:          {info.ram_total_gb} GB")
    print(f"  nvidia-smi:   {'✓ ' + info.nvidia_smi_path if info.has_nvidia_smi else '✗'}")
    print(f"  RAPL:         {'✓ domains: ' + ', '.join(info.rapl_domains) if info.has_rapl else '✗'}")
    print(f"  rocm-smi:     {'✓' if info.has_rocm_smi else '✗'}")
    print(f"  amd-smi:      {'✓' if info.has_amd_smi else '✗'}")

    if info.gpus:
        print(f"  GPUs ({len(info.gpus)}):")
        for g in info.gpus:
            print(f"    [{g.index}] {g.name} — {g.vram_mb:.0f} MB VRAM")


if __name__ == "__main__":
    print("PowerMesh Platform Detection")
    print("=" * 40)
    print_platform_report()
