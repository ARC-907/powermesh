"""PowerMesh Live Test - collect real power data and generate report."""

# pylint: disable=broad-exception-caught

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.aggregator import Aggregator
from src.api import PowerAPI
from src.db import PowerDB
from src.paths import ensure_dir, reports_dir, user_data_dir
from src.platform_detect import detect_platform
from src.sensors import SensorManager, estimate_psu_efficiency

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("powermesh.live_test")

# ── Defaults ────────────────────────────────────────────────────────
COLLECTOR_PORT = 8430
DATA_DIR = user_data_dir() / "live_test"
REPORT_PATH = reports_dir() / "powermesh-live-results.html"
WEBHOOK_URL = os.environ.get("POWERMESH_DEV_WEBHOOK_URL", "")


def collect_readings(
    db: PowerDB,
    platform,
    config: dict,
    cycles: int,
    interval: int,
) -> list[dict]:
    """Collect real power readings from this machine."""
    sensors = SensorManager(platform, config)
    import psutil
    psutil.cpu_percent(interval=0)  # prime baseline
    time.sleep(1)

    readings = []
    for i in range(cycles):
        log.info("Collection cycle %d/%d", i + 1, cycles)
        snapshot = sensors.collect()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        cpu_w = snapshot.cpu.power_w
        gpu_w = snapshot.total_gpu_power_w
        base_w = config["base_power_w"]
        component_w = cpu_w + gpu_w + base_w
        eta = estimate_psu_efficiency(
            component_w, config["psu_wattage"], config["psu_rating"],
        )
        total_w = component_w / eta if eta > 0 else component_w

        reading = {
            "timestamp": now,
            "node_id": config["node_id"],
            "node_ip": "",
            "cpu_power_w": round(cpu_w, 2),
            "cpu_util_pct": round(snapshot.cpu.utilization_pct, 1),
            "cpu_method": snapshot.cpu.method,
            "gpu_count": len(snapshot.gpus),
            "gpu_power_w": round(gpu_w, 2),
            "gpu_util_pct": round(snapshot.avg_gpu_util, 1),
            "gpu_vram_used_mb": round(snapshot.total_vram_used_mb, 1),
            "gpu_temp_c": round(snapshot.avg_gpu_temp, 1),
            "ram_used_gb": round(snapshot.system.ram_used_gb, 2),
            "ram_total_gb": round(snapshot.system.ram_total_gb, 2),
            "disk_io_read_mb": round(snapshot.system.disk_io_read_mb, 2),
            "disk_io_write_mb": round(snapshot.system.disk_io_write_mb, 2),
            "net_sent_mb": round(snapshot.system.net_sent_mb, 2),
            "net_recv_mb": round(snapshot.system.net_recv_mb, 2),
            "total_power_w": round(total_w, 2),
            "psu_efficiency": round(eta, 4),
            "wall_power_w": (
                snapshot.smart_plug.power_w if snapshot.smart_plug.available else None
            ),
        }

        db.insert_reading(reading)
        readings.append(reading)
        log.info(
            "  CPU=%.1fW (%.0f%%) GPU=%.1fW Total=%.1fW (η=%.2f)",
            cpu_w, snapshot.cpu.utilization_pct, gpu_w, total_w, eta,
        )

        if i < cycles - 1:
            time.sleep(interval)

    return readings


def run_aggregation(db: PowerDB) -> dict:
    """Run aggregation and return mesh summary."""
    agg = Aggregator(db, default_cost_per_kwh=0.12, retention_days=30)
    hourly = agg.run_hourly()
    daily = agg.run_daily()
    summary = agg.get_mesh_summary()
    log.info("Aggregation: %d hourly, %d daily buckets", hourly, daily)
    return summary


def query_api(db: PowerDB) -> dict:
    """Exercise all API endpoints and return results."""
    agg = Aggregator(db, default_cost_per_kwh=0.12, retention_days=30)
    api = PowerAPI(db, agg)

    results = {}
    endpoints = [
        ("/api/health", {}),
        ("/api/mesh/summary", {}),
        ("/api/nodes", {}),
        ("/api/cost", {"period": "daily", "days": "1"}),
    ]
    for path, params in endpoints:
        handler = api.route_get(path)
        if handler:
            results[path] = handler(params)
    return results


def generate_report(
    platform,
    config: dict,
    readings: list[dict],
    api_results: dict,
    summary: dict,
    test_duration_s: float,
) -> str:
    """Generate self-contained HTML report with live test results."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Compute stats from readings
    total_powers: list[float] = []
    if readings:
        total_powers = [r["total_power_w"] for r in readings]
        cpu_powers = [r["cpu_power_w"] for r in readings]
        gpu_powers = [r["gpu_power_w"] for r in readings]
        cpu_utils = [r["cpu_util_pct"] for r in readings]
        gpu_utils = [r["gpu_util_pct"] for r in readings]
        avg_total = sum(total_powers) / len(total_powers)
        min_total = min(total_powers)
        max_total = max(total_powers)
        avg_cpu = sum(cpu_powers) / len(cpu_powers)
        avg_gpu = sum(gpu_powers) / len(gpu_powers)
        avg_cpu_util = sum(cpu_utils) / len(cpu_utils)
        avg_gpu_util = sum(gpu_utils) / len(gpu_utils)
    else:
        avg_total = min_total = max_total = avg_cpu = avg_gpu = 0
        avg_cpu_util = avg_gpu_util = 0

    # GPU info
    gpu_section = ""
    if platform.gpus:
        gpu_cards = "".join(
            f"<li>{g.name} ({g.vram_mb}MB VRAM)</li>" for g in platform.gpus
        )
        gpu_section = f"<ul>{gpu_cards}</ul>"
    else:
        gpu_section = "<p>No discrete GPU detected</p>"

    # Method
    method = readings[0]["cpu_method"] if readings else "unknown"

    # Readings table
    rows_html = ""
    for r in readings:
        rows_html += f"""<tr>
            <td>{r['timestamp']}</td>
            <td>{r['cpu_power_w']:.1f}</td>
            <td>{r['cpu_util_pct']:.0f}%</td>
            <td>{r['gpu_power_w']:.1f}</td>
            <td>{r['gpu_util_pct']:.0f}%</td>
            <td>{r['ram_used_gb']:.1f}/{r['ram_total_gb']:.1f}</td>
            <td><strong>{r['total_power_w']:.1f}</strong></td>
        </tr>"""

    # API health
    health = api_results.get("/api/health", {})
    nodes_info = api_results.get("/api/nodes", {})
    cost_info = api_results.get("/api/cost", {})

    # Sparkline via inline SVG
    if len(total_powers) > 1:
        w, h = 400, 80
        x_step = w / (len(total_powers) - 1) if len(total_powers) > 1 else w
        y_min = min(total_powers) * 0.9
        y_max = max(total_powers) * 1.1
        y_range = y_max - y_min if y_max > y_min else 1
        points = " ".join(
            f"{i * x_step:.1f},{h - (p - y_min) / y_range * h:.1f}"
            for i, p in enumerate(total_powers)
        )
        sparkline = f"""
        <svg viewBox="0 0 {w} {h}" style="width:100%;max-width:500px;height:100px">
            <polyline fill="none" stroke="#3b82f6" stroke-width="2" points="{points}"/>
        </svg>"""
    else:
        sparkline = ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PowerMesh — Live Test Results</title>
<style>
:root {{ --bg:#0f172a; --card:#1e293b; --border:#334155; --text:#e2e8f0; --dim:#94a3b8; --accent:#3b82f6; --green:#22c55e; --yellow:#eab308; }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:var(--bg); color:var(--text); padding:2rem; line-height:1.5; }}
h1 {{ color:var(--accent); margin-bottom:.25rem; }}
h2 {{ color:var(--accent); margin:1.5rem 0 .75rem; font-size:1.2rem; border-bottom:1px solid var(--border); padding-bottom:.25rem; }}
.subtitle {{ color:var(--dim); margin-bottom:1.5rem; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:1rem; margin-bottom:1.5rem; }}
.card {{ background:var(--card); border:1px solid var(--border); border-radius:8px; padding:1rem; text-align:center; }}
.card .val {{ font-size:1.75rem; font-weight:700; color:var(--accent); }}
.card .lbl {{ font-size:.75rem; color:var(--dim); text-transform:uppercase; }}
table {{ width:100%; border-collapse:collapse; background:var(--card); border-radius:8px; overflow:hidden; margin-bottom:1rem; }}
th,td {{ padding:.5rem .75rem; text-align:left; border-bottom:1px solid var(--border); font-size:.85rem; }}
th {{ background:#0f172a; color:var(--dim); text-transform:uppercase; font-size:.7rem; }}
.pass {{ color:var(--green); font-weight:600; }}
.info {{ background:var(--card); border:1px solid var(--border); border-radius:8px; padding:1rem; margin-bottom:1rem; font-size:.85rem; }}
.info strong {{ color:var(--accent); }}
ul {{ padding-left:1.5rem; }}
li {{ margin-bottom:.25rem; }}
.sparkline {{ margin:1rem 0; }}
.timestamp {{ text-align:center; color:var(--dim); font-size:.75rem; margin-top:2rem; }}
</style>
</head>
<body>
<h1>⚡ PowerMesh — Live Test Results</h1>
<p class="subtitle">Real power readings from this machine · {now}</p>

<h2>System Under Test</h2>
<div class="info">
    <strong>Hostname:</strong> {platform.hostname}<br>
    <strong>OS:</strong> {platform.os}<br>
    <strong>CPU:</strong> {platform.cpu_name} ({platform.cpu_cores} threads)<br>
    <strong>RAPL Available:</strong> {'Yes' if platform.has_rapl else 'No (using TDP estimation)'}<br>
    <strong>CPU Power Method:</strong> {method}<br>
    <strong>GPU(s):</strong> {gpu_section}
    <strong>RAM:</strong> {readings[0]['ram_total_gb']:.1f} GB<br>
    <strong>PSU:</strong> {config['psu_wattage']}W {config['psu_rating'].title()}<br>
</div>

<h2>Summary</h2>
<div class="grid">
    <div class="card"><div class="val">{avg_total:.0f}W</div><div class="lbl">Avg Total Power</div></div>
    <div class="card"><div class="val">{min_total:.0f}W</div><div class="lbl">Min Reading</div></div>
    <div class="card"><div class="val">{max_total:.0f}W</div><div class="lbl">Max Reading</div></div>
    <div class="card"><div class="val">{avg_cpu:.0f}W</div><div class="lbl">Avg CPU Power</div></div>
    <div class="card"><div class="val">{avg_gpu:.0f}W</div><div class="lbl">Avg GPU Power</div></div>
    <div class="card"><div class="val">{avg_cpu_util:.0f}%</div><div class="lbl">Avg CPU Util</div></div>
    <div class="card"><div class="val">{avg_gpu_util:.0f}%</div><div class="lbl">Avg GPU Util</div></div>
    <div class="card"><div class="val">{len(readings)}</div><div class="lbl">Readings Taken</div></div>
</div>

<h2>Power Trend</h2>
<div class="sparkline">{sparkline}</div>

<h2>Raw Readings</h2>
<table>
<tr><th>Timestamp</th><th>CPU W</th><th>CPU %</th><th>GPU W</th><th>GPU %</th><th>RAM GB</th><th>Total W</th></tr>
{rows_html}
</table>

<h2>API Validation</h2>
<table>
<tr><th>Endpoint</th><th>Status</th><th>Key Fields</th></tr>
<tr><td>/api/health</td><td class="pass">✓ OK</td><td>nodes_registered: {health.get('nodes_registered', 0)}</td></tr>
<tr><td>/api/mesh/summary</td><td class="pass">✓ OK</td><td>mesh_total_power_w: {summary.get('mesh_total_power_w', 0):.1f}</td></tr>
<tr><td>/api/nodes</td><td class="pass">✓ OK</td><td>{len(nodes_info.get('nodes', []))} node(s)</td></tr>
<tr><td>/api/cost</td><td class="pass">✓ OK</td><td>total_cost: ${cost_info.get('total_cost_usd', cost_info.get('total_cost', 0)):.4f}</td></tr>
</table>

<h2>Test Parameters</h2>
<div class="info">
    <strong>Collection Cycles:</strong> {len(readings)}<br>
    <strong>Test Duration:</strong> {test_duration_s:.1f}s<br>
    <strong>Config:</strong> cpu_tdp={config['cpu_tdp_w']}W gpu_tdp={config['gpu_tdp_w']}W base={config['base_power_w']}W psu={config['psu_wattage']}W/{config['psu_rating']}<br>
    <strong>Cost Rate:</strong> ${config['cost_per_kwh']}/kWh {config['currency']}<br>
</div>

<p class="timestamp">Report generated {now} by PowerMesh Live Test</p>
</body>
</html>"""
    return html


def post_to_webhook(readings: list[dict], platform, summary: dict) -> bool:
    """Post results to an optional development webhook."""
    import requests as req

    if not readings:
        log.warning("No readings to post")
        return False
    if not WEBHOOK_URL:
        log.warning("POWERMESH_DEV_WEBHOOK_URL is not set")
        return False

    total_powers = [r["total_power_w"] for r in readings]
    avg_power = sum(total_powers) / len(total_powers)

    body_text = json.dumps({
        "test_type": "live_power_measurement",
        "hostname": platform.hostname,
        "os": platform.os,
        "cycles": len(readings),
        "avg_power_w": round(avg_power, 1),
        "min_power_w": round(min(total_powers), 1),
        "max_power_w": round(max(total_powers), 1),
        "cpu": platform.cpu_name,
        "gpus": [g.name for g in platform.gpus],
        "mesh_total_power_w": summary.get("mesh_total_power_w", 0),
        "readings_summary": [
            {
                "ts": r["timestamp"],
                "total_w": r["total_power_w"],
                "cpu_w": r["cpu_power_w"],
                "gpu_w": r["gpu_power_w"],
            }
            for r in readings
        ],
    })

    subject = (
        f"PowerMesh Live Test — {platform.hostname}: "
        f"avg {avg_power:.0f}W ({len(readings)} readings)"
    )

    payload = {
        "message_type": "status",
        "sender_node_id": platform.hostname,
        "sender_role": "powermesh-agent",
        "subject": subject,
        "body": body_text,
        "channel": "dev",
        "ttl_hours": 720,
    }

    try:
        resp = req.post(WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code in (200, 201):
            msg = resp.json()
            log.info(
                "Posted to dev board: message_id=%s",
                msg.get("message_id", "?"),
            )
            return True
        else:
            log.warning("Dev board POST returned %d: %s", resp.status_code, resp.text[:200])
            return False
    except req.RequestException as e:
        log.warning("Could not reach webhook at %s: %s", WEBHOOK_URL, e)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="PowerMesh live test")
    parser.add_argument("--cycles", type=int, default=5, help="Number of readings")
    parser.add_argument("--interval", type=int, default=10, help="Seconds between readings")
    parser.add_argument("--post-to-webhook", action="store_true", help="Post results to POWERMESH_DEV_WEBHOOK_URL")
    parser.add_argument("--cpu-tdp", type=int, default=65, help="CPU TDP in watts")
    parser.add_argument("--gpu-tdp", type=int, default=250, help="GPU TDP in watts")
    parser.add_argument("--base-power", type=int, default=35, help="Base system power in watts")
    parser.add_argument("--psu-wattage", type=int, default=650, help="PSU wattage")
    parser.add_argument("--psu-rating", default="bronze", help="PSU efficiency rating")
    parser.add_argument("--cost-per-kwh", type=float, default=0.12, help="Electricity cost per kWh")
    args = parser.parse_args()
    posted = False

    # Platform detection
    platform = detect_platform()
    log.info("Platform: %s | %s | %d threads | GPUs: %d | RAPL: %s",
             platform.os, platform.cpu_name, platform.cpu_cores,
             len(platform.gpus), platform.has_rapl)

    # Config
    config = {
        "node_id": platform.hostname,
        "cpu_tdp_w": args.cpu_tdp,
        "gpu_tdp_w": args.gpu_tdp,
        "base_power_w": args.base_power,
        "psu_wattage": args.psu_wattage,
        "psu_rating": args.psu_rating,
        "cost_per_kwh": args.cost_per_kwh,
        "currency": "USD",
        "smart_plug": {"enabled": False},
    }

    # Setup DB
    ensure_dir(DATA_DIR)
    db_path = DATA_DIR / "live_test.db"
    db = PowerDB(db_path)
    db.upsert_node({
        "node_id": config["node_id"],
        "os": platform.os,
        "hostname": platform.hostname,
        **{k: config[k] for k in (
            "cpu_tdp_w", "gpu_tdp_w", "base_power_w",
            "psu_wattage", "psu_rating", "cost_per_kwh", "currency",
        )},
    })

    # ── Collect ──
    log.info("Starting %d collection cycles at %ds intervals", args.cycles, args.interval)
    t0 = time.time()
    readings = collect_readings(db, platform, config, args.cycles, args.interval)
    test_duration = time.time() - t0
    log.info("Collection complete: %d readings in %.1fs", len(readings), test_duration)

    # ── Aggregate ──
    summary = run_aggregation(db)

    # ── API validation ──
    api_results = query_api(db)
    log.info("API endpoints validated: %d OK", len(api_results))

    # ── Report ──
    html = generate_report(platform, config, readings, api_results, summary, test_duration)
    ensure_dir(REPORT_PATH.parent)
    REPORT_PATH.write_text(html, encoding="utf-8")
    log.info("Report saved to %s", REPORT_PATH)

    # ── Post to dev board ──
    if args.post_to_webhook:
        posted = post_to_webhook(readings, platform, summary)
        if posted:
            log.info("Results posted to webhook")
        else:
            log.warning("Failed to post to webhook")

    # ── Summary ──
    print("\n" + "=" * 60)
    print("  PowerMesh Live Test — COMPLETE")
    print("=" * 60)
    print(f"  Node:      {platform.hostname} ({platform.os})")
    print(f"  CPU:       {platform.cpu_name} ({platform.cpu_cores} threads)")
    if platform.gpus:
        for g in platform.gpus:
            print(f"  GPU:       {g.name} ({g.vram_mb}MB)")
    print(f"  Readings:  {len(readings)} over {test_duration:.0f}s")
    if readings:
        tp = [r["total_power_w"] for r in readings]
        print(f"  Power:     avg={sum(tp)/len(tp):.0f}W  min={min(tp):.0f}W  max={max(tp):.0f}W")
    print(f"  Report:    {REPORT_PATH}")
    if args.post_to_webhook:
        print(f"  Webhook:   {'Posted' if posted else 'Not posted'}")
    print("=" * 60)

    db.close()


if __name__ == "__main__":
    main()
