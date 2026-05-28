"""PowerMesh Dashboard — single-file HTML dashboard rendered server-side."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def render_dashboard(summary: dict[str, Any], app_info: dict[str, Any] | None = None) -> str:
    """Render a self-contained HTML dashboard from mesh summary data."""
    app_info = app_info or {}
    edition = _esc(str(app_info.get("edition", "Full")))
    version = _esc(str(app_info.get("version", "0.1.0")))
    nodes_html = ""
    for node in summary.get("nodes", []):
        status_color = "#22c55e" if node.get("status") == "online" else "#ef4444"
        status_dot = f'<span style="color:{status_color}">●</span>'

        gpu_info = ""
        if node.get("gpu_power_w", 0) > 0:
            gpu_info = f"""
            <div class="metric">
                <span class="label">GPU</span>
                <span class="value">{node.get('gpu_power_w', 0):.1f}W</span>
                <span class="sub">{node.get('gpu_util_pct', 0):.0f}% · {node.get('gpu_temp_c', 0):.0f}°C</span>
            </div>"""

        nodes_html += f"""
        <div class="node-card">
            <div class="node-header">
                {status_dot} <strong>{_esc(node.get('hostname', node.get('node_id', '?')))}</strong>
            </div>
            <div class="node-body">
                <div class="metric">
                    <span class="label">Total</span>
                    <span class="value big">{node.get('total_power_w', 0):.1f}W</span>
                </div>
                <div class="metric">
                    <span class="label">CPU</span>
                    <span class="value">{node.get('cpu_power_w', 0):.1f}W</span>
                    <span class="sub">{node.get('cpu_util_pct', 0):.0f}%</span>
                </div>
                {gpu_info}
            </div>
            <div class="node-footer">
                Last seen: {_esc(str(node.get('last_seen', 'never')))}
            </div>
        </div>"""

    if not nodes_html:
        nodes_html = """
        <div class="empty-state">
            <strong>No nodes have reported yet.</strong>
            <span>Start an agent or run Lite mode to collect the first reading.</span>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PowerMesh Dashboard</title>
<style>
:root {{
    --bg: #0f172a; --card-bg: #1e293b; --border: #334155;
    --text: #e2e8f0; --text-dim: #94a3b8; --accent: #3b82f6;
    --green: #22c55e; --yellow: #eab308; --red: #ef4444;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg); color: var(--text); padding: 1.5rem;
}}
h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
.topbar {{ display:flex; align-items:flex-start; justify-content:space-between; gap:1rem; flex-wrap:wrap; margin-bottom:1.5rem; }}
.subtitle {{ color: var(--text-dim); font-size: 0.875rem; }}
.badge {{ display:inline-flex; align-items:center; gap:.35rem; border:1px solid var(--border); border-radius:999px; padding:.25rem .65rem; color:var(--text-dim); font-size:.75rem; }}
.toolbar {{ display:flex; gap:.5rem; flex-wrap:wrap; margin-bottom:1.5rem; }}
.button {{ background:var(--card-bg); color:var(--text); border:1px solid var(--border); border-radius:6px; padding:.55rem .75rem; text-decoration:none; font-size:.85rem; cursor:pointer; }}
.button:hover {{ border-color:var(--accent); }}
.summary-bar {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 1rem; margin-bottom: 2rem;
}}
.summary-card {{
    background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 8px; padding: 1rem; text-align: center;
}}
.summary-card .val {{ font-size: 1.75rem; font-weight: 700; color: var(--accent); }}
.summary-card .lbl {{ font-size: 0.75rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; }}
.nodes-grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 1rem;
}}
.node-card {{
    background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 8px; overflow: hidden;
}}
.node-header {{
    padding: 0.75rem 1rem; border-bottom: 1px solid var(--border);
    font-size: 0.95rem;
}}
.node-body {{ padding: 1rem; display: flex; gap: 1rem; flex-wrap: wrap; }}
.metric {{ display: flex; flex-direction: column; min-width: 70px; }}
.metric .label {{ font-size: 0.7rem; color: var(--text-dim); text-transform: uppercase; }}
.metric .value {{ font-size: 1.1rem; font-weight: 600; }}
.metric .value.big {{ font-size: 1.5rem; color: var(--accent); }}
.metric .sub {{ font-size: 0.75rem; color: var(--text-dim); }}
.node-footer {{
    padding: 0.5rem 1rem; border-top: 1px solid var(--border);
    font-size: 0.7rem; color: var(--text-dim);
}}
.refresh-hint {{
    text-align: center; margin-top: 2rem; font-size: 0.75rem; color: var(--text-dim);
}}
.empty-state {{ background: var(--card-bg); border:1px dashed var(--border); border-radius:8px; padding:2rem; display:flex; flex-direction:column; gap:.35rem; color:var(--text-dim); grid-column:1/-1; }}
</style>
<script>
let refreshTimer = null;
function scheduleRefresh() {{
  const enabled = localStorage.getItem('powermesh-refresh') !== 'paused';
  document.documentElement.dataset.refresh = enabled ? 'running' : 'paused';
  const button = document.getElementById('refresh-toggle');
  if (button) button.textContent = enabled ? 'Pause refresh' : 'Resume refresh';
  if (refreshTimer) clearTimeout(refreshTimer);
  if (enabled) refreshTimer = setTimeout(() => window.location.reload(), 10000);
}}
function toggleRefresh() {{
  const enabled = localStorage.getItem('powermesh-refresh') !== 'paused';
  localStorage.setItem('powermesh-refresh', enabled ? 'paused' : 'running');
  scheduleRefresh();
}}
window.addEventListener('load', scheduleRefresh);
</script>
</head>
<body>
<div class="topbar">
    <div>
        <h1>⚡ PowerMesh</h1>
        <p class="subtitle">
            {summary.get('nodes_online', 0)}/{summary.get('node_count', 0)} nodes online · auto-refresh 10s
        </p>
    </div>
    <span class="badge">{edition} · v{version}</span>
</div>

<div class="toolbar">
    <button id="refresh-toggle" class="button" onclick="toggleRefresh()">Pause refresh</button>
    <form method="post" action="/api/refresh"><button class="button" type="submit">Recompute aggregates</button></form>
    <a class="button" href="/api/export?format=csv&range=24h">Export CSV</a>
    <a class="button" href="/api/export?format=json&range=24h">Export JSON</a>
    <a class="button" href="/report">Report</a>
    <a class="button" href="/settings">Settings</a>
</div>

<div class="summary-bar">
    <div class="summary-card">
        <div class="val">{summary.get('mesh_total_power_w', 0):.0f}W</div>
        <div class="lbl">Total Power</div>
    </div>
    <div class="summary-card">
        <div class="val">{summary.get('mesh_kwh_per_day', 0):.1f}</div>
        <div class="lbl">kWh / Day</div>
    </div>
    <div class="summary-card">
        <div class="val">${summary.get('mesh_cost_per_day', 0):.2f}</div>
        <div class="lbl">Cost / Day</div>
    </div>
    <div class="summary-card">
        <div class="val">${summary.get('mesh_cost_per_month', 0):.2f}</div>
        <div class="lbl">Cost / Month</div>
    </div>
</div>

<div class="nodes-grid">
{nodes_html}
</div>

<p class="refresh-hint">Dashboard auto-refreshes every 10 seconds · JSON API at /api/mesh/summary</p>
</body>
</html>"""


def render_settings(config: dict[str, Any], writable: bool = True) -> str:
    rows = "".join(
        f"<tr><th>{_esc(str(key))}</th><td><code>{_esc(str(value))}</code></td></tr>"
        for key, value in sorted(config.items())
    )
    disabled = "" if writable else "disabled"
    note = "" if writable else "<p class='warn'>Settings writes are only available from localhost.</p>"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PowerMesh Settings</title>{_base_styles()}</head><body>
<header><h1>Settings</h1><a class="button" href="/">Dashboard</a></header>
{note}
<section class="panel">
<h2>Effective Configuration</h2>
<table>{rows}</table>
</section>
<section class="panel">
<h2>Local Overrides</h2>
<form method="post" action="/api/settings" class="settings-form">
<label>Cost per kWh <input name="cost_per_kwh_default" type="number" step="0.0001" min="0" {disabled}></label>
<label>Retention days <input name="retention_days" type="number" step="1" min="1" {disabled}></label>
<label>Aggregation interval minutes <input name="aggregation_interval_m" type="number" step="1" min="1" {disabled}></label>
<button class="button" type="submit" {disabled}>Save local overrides</button>
</form>
</section>
</body></html>"""


def render_report(summary: dict[str, Any], app_info: dict[str, Any] | None = None) -> str:
    app_info = app_info or {}
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    node_rows = "".join(
        "<tr>"
        f"<td>{_esc(str(node.get('node_id', '')))}</td>"
        f"<td>{_esc(str(node.get('status', '')))}</td>"
        f"<td>{node.get('total_power_w', 0):.1f} W</td>"
        f"<td>{_esc(str(node.get('last_seen', 'never')))}</td>"
        "</tr>"
        for node in summary.get("nodes", [])
    ) or "<tr><td colspan='4'>No nodes have reported yet.</td></tr>"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PowerMesh Report</title>{_base_styles()}</head><body>
<header><div><h1>PowerMesh Report</h1><p class="muted">Generated {generated} · { _esc(str(app_info.get('edition', 'Full'))) } v{ _esc(str(app_info.get('version', '0.1.0'))) }</p></div><a class="button" href="/">Dashboard</a></header>
<section class="cards">
<div class="card"><strong>{summary.get('mesh_total_power_w', 0):.0f} W</strong><span>Total power</span></div>
<div class="card"><strong>{summary.get('mesh_kwh_per_day', 0):.2f}</strong><span>kWh/day</span></div>
<div class="card"><strong>${summary.get('mesh_cost_per_day', 0):.2f}</strong><span>Cost/day</span></div>
<div class="card"><strong>${summary.get('mesh_cost_per_month', 0):.2f}</strong><span>Cost/month</span></div>
</section>
<section class="panel"><h2>Nodes</h2><table><tr><th>Node</th><th>Status</th><th>Power</th><th>Last seen</th></tr>{node_rows}</table></section>
</body></html>"""


def _base_styles() -> str:
    return """<style>
:root { --bg:#0f172a; --card:#1e293b; --border:#334155; --text:#e2e8f0; --muted:#94a3b8; --accent:#3b82f6; --warn:#eab308; }
* { box-sizing:border-box; } body { margin:0; padding:1.5rem; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; line-height:1.5; }
header { display:flex; justify-content:space-between; gap:1rem; align-items:flex-start; flex-wrap:wrap; margin-bottom:1.5rem; }
h1 { margin:0; } h2 { margin-top:0; } .muted { color:var(--muted); margin:.25rem 0 0; }
.button { background:var(--card); color:var(--text); border:1px solid var(--border); border-radius:6px; padding:.55rem .75rem; text-decoration:none; cursor:pointer; }
.panel, .card { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:1rem; margin-bottom:1rem; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:1rem; margin-bottom:1rem; }
.card { display:flex; flex-direction:column; gap:.25rem; } .card strong { font-size:1.5rem; color:var(--accent); } .card span { color:var(--muted); font-size:.8rem; text-transform:uppercase; }
table { width:100%; border-collapse:collapse; } th, td { border-bottom:1px solid var(--border); text-align:left; padding:.5rem; vertical-align:top; } th { color:var(--muted); }
code, input { font:inherit; } input { width:100%; margin-top:.25rem; background:#0f172a; color:var(--text); border:1px solid var(--border); border-radius:6px; padding:.5rem; }
.settings-form { display:grid; gap:1rem; max-width:420px; } .warn { color:var(--warn); }
@media print { body { background:white; color:black; } .button { display:none; } .panel, .card { border-color:#ddd; background:white; } }
</style>"""


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
