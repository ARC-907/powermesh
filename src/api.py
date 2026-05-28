"""PowerMesh REST API — route table for collector HTTP server."""

from __future__ import annotations

import logging
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .aggregator import Aggregator
from .config import masked_config, save_user_config
from .dashboard import render_dashboard, render_report, render_settings
from .db import PowerDB

log = logging.getLogger("powermesh.api")

GetHandler = Callable[[dict[str, str]], Any]
PostHandler = Callable[[dict[str, Any]], Any]


@dataclass
class ApiResponse:
    body: str
    content_type: str = "text/plain"
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)


class PowerAPI:
    """Route table mapping GET paths to handler functions."""

    def __init__(
        self,
        db: PowerDB,
        aggregator: Aggregator,
        config: dict[str, Any] | None = None,
        app_info: dict[str, Any] | None = None,
    ) -> None:
        self.db = db
        self.aggregator = aggregator
        self.config = config or {}
        self.app_info = app_info or {"edition": "Full", "version": "0.1.0"}
        self._routes: dict[str, GetHandler] = {
            "/": self._dashboard,
            "/settings": self._settings_page,
            "/report": self._report,
            "/api/health": self._health,
            "/api/settings": self._settings_json,
            "/api/export": self._export,
            "/api/mesh/summary": self._mesh_summary,
            "/api/nodes": self._nodes,
            "/api/node/latest": self._node_latest,
            "/api/node/history": self._node_history,
            "/api/aggregates": self._aggregates,
            "/api/cost": self._cost_summary,
        }
        self._post_routes: dict[str, PostHandler] = {
            "/api/refresh": self._refresh,
            "/api/settings": self._settings_update,
        }

    def route_get(self, path: str) -> GetHandler | None:
        return self._routes.get(path)

    def route_post(self, path: str) -> PostHandler | None:
        return self._post_routes.get(path)

    def _health(self, _params: dict) -> dict:
        nodes = self.db.get_all_node_ids()
        return {
            "status": "ok",
            "service": "powermesh-collector",
            "edition": self.app_info.get("edition", "Full"),
            "nodes_registered": len(nodes),
        }

    def _mesh_summary(self, _params: dict) -> dict:
        return self.aggregator.get_mesh_summary()

    def _nodes(self, _params: dict) -> dict:
        nodes = self.db.get_all_node_ids()
        result = []
        for nid in nodes:
            cfg = self.db.get_node(nid)
            latest = self.db.get_latest_reading(nid)
            result.append({
                "node_id": nid,
                "config": cfg,
                "latest_reading": latest,
            })
        return {"nodes": result}

    def _node_latest(self, params: dict) -> dict:
        node_id = params.get("node_id", "")
        if not node_id:
            return {"error": "node_id required"}
        reading = self.db.get_latest_reading(node_id)
        return {"node_id": node_id, "reading": reading}

    def _node_history(self, params: dict) -> dict:
        node_id = params.get("node_id", "")
        if not node_id:
            return {"error": "node_id required"}
        limit = int(params.get("limit", "100"))
        limit = min(limit, 1440)  # Max 24h of 1-minute readings
        readings = self.db.get_readings(node_id=node_id, limit=limit)
        return {"node_id": node_id, "count": len(readings), "readings": readings}

    def _aggregates(self, params: dict) -> dict:
        node_id = params.get("node_id", "")
        period = params.get("period", "hourly")
        limit = int(params.get("limit", "24"))
        limit = min(limit, 720)

        if node_id:
            aggs = self.db.get_aggregates(node_id=node_id, period_type=period, limit=limit)
        else:
            # All nodes
            nodes = self.db.get_all_node_ids()
            aggs = []
            for nid in nodes:
                aggs.extend(self.db.get_aggregates(node_id=nid, period_type=period, limit=limit))

        return {"period": period, "count": len(aggs), "aggregates": aggs}

    def _cost_summary(self, params: dict) -> dict:
        period = params.get("period", "daily")
        days = int(params.get("days", "30"))
        nodes = self.db.get_all_node_ids()

        total_cost = 0.0
        total_energy = 0.0
        breakdown = []

        for nid in nodes:
            aggs = self.db.get_aggregates(node_id=nid, period_type=period, limit=days)
            node_energy = sum(a.get("energy_wh", 0) for a in aggs)
            node_cost = sum(a.get("cost", 0) for a in aggs)
            total_energy += node_energy
            total_cost += node_cost
            breakdown.append({
                "node_id": nid,
                "energy_kwh": round(node_energy / 1000, 3),
                "cost": round(node_cost, 4),
            })

        return {
            "period": period,
            "days": days,
            "total_energy_kwh": round(total_energy / 1000, 3),
            "total_cost": round(total_cost, 4),
            "by_node": breakdown,
        }

    def _dashboard(self, _params: dict) -> str:
        summary = self.aggregator.get_mesh_summary()
        return render_dashboard(summary, self.app_info)

    def _settings_page(self, _params: dict) -> str:
        return render_settings(masked_config(self.config), writable=True)

    def _settings_json(self, _params: dict) -> dict:
        return {"settings": masked_config(self.config), "sources": self.config.get("_sources", [])}

    def _report(self, _params: dict) -> str:
        return render_report(self.aggregator.get_mesh_summary(), self.app_info)

    def _export(self, params: dict) -> ApiResponse | dict:
        export_format = params.get("format", "json").lower()
        node_id = params.get("node_id") or None
        from_ts = _range_start(params.get("range", "24h"))
        readings = self.db.get_readings(node_id=node_id, from_ts=from_ts, limit=100000)
        filename = f"powermesh-readings-{params.get('range', '24h')}.{export_format}"

        if export_format == "csv":
            body = self.db.export_readings_csv(node_id=node_id, from_ts=from_ts, limit=100000)
            return ApiResponse(
                body=body,
                content_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        if export_format == "json":
            return ApiResponse(
                body=json.dumps({"count": len(readings), "readings": list(reversed(readings))}, default=str, indent=2),
                content_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        return {"error": "format must be csv or json"}

    def _refresh(self, _payload: dict[str, Any]) -> dict:
        hourly = self.aggregator.run_hourly()
        daily = self.aggregator.run_daily()
        pruned = self.aggregator.prune_old_readings()
        return {"status": "ok", "hourly": hourly, "daily": daily, "pruned": pruned}

    def _settings_update(self, payload: dict[str, Any]) -> dict:
        allowed = {
            "cost_per_kwh_default": float,
            "retention_days": int,
            "aggregation_interval_m": int,
        }
        changed: dict[str, Any] = {}
        for key, caster in allowed.items():
            if key not in payload or payload[key] in (None, ""):
                continue
            changed[key] = caster(payload[key])
        self.config.update(changed)
        path = save_user_config("mesh.yaml", self.config)
        return {"status": "ok", "changed": changed, "saved_to": str(path)}


def _range_start(range_name: str) -> str:
    now = datetime.now(timezone.utc)
    deltas = {
        "1h": timedelta(hours=1),
        "24h": timedelta(days=1),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    start = now - deltas.get(range_name, deltas["24h"])
    return start.strftime("%Y-%m-%dT%H:%M:%SZ")
