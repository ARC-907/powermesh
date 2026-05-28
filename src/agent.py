"""PowerMesh Agent — collect power data, buffer locally, push to central collector."""

# pylint: disable=broad-exception-caught

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .config import load_node_config
from .db import PowerDB
from .logging_utils import setup_logging
from .platform_detect import PlatformInfo, detect_platform
from .sensors import PowerSnapshot, SensorManager, estimate_psu_efficiency

log = logging.getLogger("powermesh.agent")

class PowerAgent:
    """Daemon that collects power readings and pushes to central collector."""

    def __init__(self, config_path: str | Path | None = None, config: dict[str, Any] | None = None) -> None:
        self.config = load_node_config(config_path=config_path, config=config)
        self.platform: PlatformInfo = detect_platform()

        if not self.config["node_id"]:
            self.config["node_id"] = self.platform.hostname

        data_dir = Path(self.config["data_dir"])
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = PowerDB(data_dir / "local.db")
        self.sensors = SensorManager(self.platform, self.config)
        self._running = False

        # Register node config locally
        self.db.upsert_node({
            "node_id": self.config["node_id"],
            "os": self.platform.os,
            "cpu_tdp_w": self.config["cpu_tdp_w"],
            "gpu_tdp_w": self.config["gpu_tdp_w"],
            "base_power_w": self.config["base_power_w"],
            "psu_wattage": self.config["psu_wattage"],
            "psu_rating": self.config["psu_rating"],
            "cost_per_kwh": self.config["cost_per_kwh"],
            "currency": self.config["currency"],
        })

    def collect_once(self) -> dict[str, Any]:
        """Take a single power snapshot and compute total system power."""
        snapshot: PowerSnapshot = self.sensors.collect()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Component power
        cpu_w = snapshot.cpu.power_w
        gpu_w = snapshot.total_gpu_power_w
        base_w = self.config["base_power_w"]
        component_w = cpu_w + gpu_w + base_w

        # PSU efficiency
        eta = estimate_psu_efficiency(
            component_w,
            self.config["psu_wattage"],
            self.config["psu_rating"],
        )
        total_w = component_w / eta if eta > 0 else component_w

        reading = {
            "timestamp": now,
            "node_id": self.config["node_id"],
            "node_ip": "",
            "cpu_power_w": cpu_w,
            "cpu_util_pct": snapshot.cpu.utilization_pct,
            "cpu_method": snapshot.cpu.method,
            "gpu_count": len(snapshot.gpus),
            "gpu_power_w": round(gpu_w, 2),
            "gpu_util_pct": round(snapshot.avg_gpu_util, 1),
            "gpu_vram_used_mb": round(snapshot.total_vram_used_mb, 1),
            "gpu_temp_c": round(snapshot.avg_gpu_temp, 1),
            "ram_used_gb": snapshot.system.ram_used_gb,
            "ram_total_gb": snapshot.system.ram_total_gb,
            "disk_io_read_mb": snapshot.system.disk_io_read_mb,
            "disk_io_write_mb": snapshot.system.disk_io_write_mb,
            "net_sent_mb": snapshot.system.net_sent_mb,
            "net_recv_mb": snapshot.system.net_recv_mb,
            "total_power_w": round(total_w, 2),
            "psu_efficiency": round(eta, 4),
            "wall_power_w": snapshot.smart_plug.power_w if snapshot.smart_plug.available else None,
        }

        self.db.insert_reading(reading)
        log.debug(
            "Collected: CPU=%.1fW GPU=%.1fW Total=%.1fW (η=%.2f)",
            cpu_w, gpu_w, total_w, eta,
        )
        return reading

    def push_to_collector(self) -> int:
        """Push buffered readings to central collector. Returns count pushed."""
        collector_url = self.config.get("collector_url", "")
        if not collector_url:
            return 0

        batch_size = self.config.get("push_batch_size", 10)
        readings = self.db.get_readings(
            node_id=self.config["node_id"], limit=batch_size
        )
        if not readings:
            return 0

        url = f"{collector_url.rstrip('/')}/api/power/ingest"
        payload = {
            "node_id": self.config["node_id"],
            "readings": readings,
        }

        headers = {"Content-Type": "application/json"}
        auth_token = self.config.get("auth_token", "")
        if auth_token:
            body = json.dumps(payload, sort_keys=True)
            sig = hmac.new(
                auth_token.encode(), body.encode(), hashlib.sha256
            ).hexdigest()
            headers["X-PowerMesh-Signature"] = sig

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                accepted = data.get("accepted", 0)
                log.info("Pushed %d readings to collector", accepted)
                return accepted
            else:
                log.warning(
                    "Collector returned %d: %s", resp.status_code, resp.text[:200]
                )
        except requests.RequestException as e:
            log.warning("Push failed (will retry): %s", e)

        return 0

    def run(self) -> None:
        """Main loop: collect → buffer → push."""
        self._running = True
        interval = self.config["collection_interval_s"]

        log.info(
            "PowerMesh agent started — node=%s interval=%ds collector=%s",
            self.config["node_id"],
            interval,
            self.config.get("collector_url", "(local only)"),
        )
        log.info(
            "Platform: %s | CPU: %s (%d threads) | GPUs: %d | RAPL: %s",
            self.platform.os,
            self.platform.cpu_name,
            self.platform.cpu_cores,
            len(self.platform.gpus),
            "yes" if self.platform.has_rapl else "no",
        )

        # Initial CPU utilization sample (psutil needs a baseline)
        import psutil
        psutil.cpu_percent(interval=0)

        cycle = 0
        while self._running:
            try:
                self.collect_once()
                cycle += 1

                # Push every N cycles (batch efficiency)
                if cycle % self.config.get("push_batch_size", 10) == 0:
                    self.push_to_collector()

            except Exception as e:
                log.error("Collection cycle failed: %s", e, exc_info=True)

            # Sleep in small increments for responsive shutdown
            for _ in range(interval):
                if not self._running:
                    break
                time.sleep(1)

        # Final push on shutdown
        self.push_to_collector()
        self.db.close()
        log.info("Agent stopped")

    def stop(self) -> None:
        self._running = False


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config/node.yaml")
    config = load_node_config(config_path=config_path)
    setup_logging("agent", config["data_dir"], config.get("log_level", "INFO"))
    agent = PowerAgent(config=config)

    def _signal_handler(_sig: int, _frame: Any) -> None:
        log.info("Shutdown signal received")
        agent.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    agent.run()


if __name__ == "__main__":
    main()
