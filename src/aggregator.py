"""PowerMesh Aggregator — roll up raw readings into hourly/daily aggregates."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .db import PowerDB

log = logging.getLogger("powermesh.aggregator")


class Aggregator:
    """Compute energy (Wh/kWh) and cost aggregates from raw power readings."""

    def __init__(
        self,
        db: PowerDB,
        default_cost_per_kwh: float = 0.12,
        retention_days: int = 30,
    ) -> None:
        self.db = db
        self.default_cost = default_cost_per_kwh
        self.retention_days = retention_days

    def run_hourly(self) -> int:
        """Roll up raw readings into hourly buckets. Returns count of aggregates created."""
        return self._run_period("hourly", hours=1)

    def run_daily(self) -> int:
        """Roll up raw readings into daily buckets."""
        return self._run_period("daily", hours=24)

    def _run_period(self, period_type: str, hours: int) -> int:
        nodes = self.db.get_all_node_ids()
        now = datetime.now(timezone.utc)
        count = 0

        for node_id in nodes:
            # Find latest aggregate to know where to start
            latest = self.db.get_latest_aggregate(node_id, period_type)
            if latest:
                start = _parse_utc(latest["period_end"])
            else:
                # Start from oldest reading
                oldest = self.db.get_oldest_reading_time(node_id)
                if not oldest:
                    continue
                start = _parse_utc(oldest).replace(
                    minute=0, second=0, microsecond=0
                )

            # Step through time windows
            window = timedelta(hours=hours)
            bucket_start = start

            while bucket_start + window <= now:
                bucket_end = bucket_start + window
                readings = self.db.get_readings_in_range(
                    node_id,
                    bucket_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    bucket_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                )

                if readings:
                    agg = self._compute_aggregate(
                        node_id, readings, bucket_start, bucket_end,
                        period_type, hours,
                    )
                    self.db.insert_aggregate(agg)
                    count += 1

                bucket_start = bucket_end

        if count:
            log.info("Created %d %s aggregates", count, period_type)
        return count

    def _compute_aggregate(
        self,
        node_id: str,
        readings: list[dict],
        start: datetime,
        end: datetime,
        period_type: str,
        hours: float,
    ) -> dict:
        powers = [r.get("total_power_w", 0) for r in readings]
        cpu_powers = [r.get("cpu_power_w", 0) for r in readings]
        gpu_powers = [r.get("gpu_power_w", 0) for r in readings]
        cpu_utils = [r.get("cpu_util_pct", 0) for r in readings]
        gpu_utils = [r.get("gpu_util_pct", 0) for r in readings]
        gpu_temps = [r.get("gpu_temp_c", 0) for r in readings if r.get("gpu_temp_c")]

        n = len(powers)
        avg_power = sum(powers) / n if n else 0
        energy_wh = avg_power * hours  # W × h = Wh

        # Get cost rate for this node, or use default
        node_cfg = self.db.get_node(node_id)
        rate = self.default_cost
        currency = "USD"
        if node_cfg:
            rate = node_cfg.get("cost_per_kwh", self.default_cost)
            currency = node_cfg.get("currency", "USD")
        cost = (energy_wh / 1000) * rate

        return {
            "node_id": node_id,
            "period_start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "period_end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "period_type": period_type,
            "avg_power_w": round(avg_power, 2),
            "min_power_w": round(min(powers), 2) if powers else 0,
            "max_power_w": round(max(powers), 2) if powers else 0,
            "avg_cpu_w": round(sum(cpu_powers) / n, 2) if n else 0,
            "avg_gpu_w": round(sum(gpu_powers) / n, 2) if n else 0,
            "avg_cpu_util": round(sum(cpu_utils) / n, 1) if n else 0,
            "avg_gpu_util": round(sum(gpu_utils) / n, 1) if n else 0,
            "avg_gpu_temp": round(sum(gpu_temps) / len(gpu_temps), 1) if gpu_temps else 0,
            "energy_wh": round(energy_wh, 2),
            "cost": round(cost, 6),
            "currency": currency,
            "reading_count": n,
        }
    def prune_old_readings(self) -> int:
        """Delete raw readings older than retention_days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        count = self.db.prune_readings(cutoff_str)
        if count:
            log.info("Pruned %d readings older than %s", count, cutoff_str)
        return count

    def get_mesh_summary(self) -> dict:
        """Get current snapshot of all nodes for dashboard."""
        nodes = self.db.get_all_node_ids()
        total_power = 0.0
        node_summaries = []

        for node_id in nodes:
            latest = self.db.get_latest_reading(node_id)
            node_cfg = self.db.get_node(node_id)

            if latest:
                total_w = latest.get("total_power_w", 0)
                total_power += total_w
                node_summaries.append({
                    "node_id": node_id,
                    "hostname": node_cfg.get("hostname", node_id) if node_cfg else node_id,
                    "status": "online",
                    "total_power_w": total_w,
                    "cpu_power_w": latest.get("cpu_power_w", 0),
                    "gpu_power_w": latest.get("gpu_power_w", 0),
                    "cpu_util_pct": latest.get("cpu_util_pct", 0),
                    "gpu_util_pct": latest.get("gpu_util_pct", 0),
                    "gpu_temp_c": latest.get("gpu_temp_c", 0),
                    "last_seen": latest.get("timestamp", ""),
                })
            else:
                node_summaries.append({
                    "node_id": node_id,
                    "hostname": node_cfg.get("hostname", node_id) if node_cfg else node_id,
                    "status": "offline",
                    "total_power_w": 0,
                })

        # Cost projections
        kwh_per_day = (total_power * 24) / 1000
        rate = self.default_cost

        return {
            "mesh_total_power_w": round(total_power, 2),
            "mesh_kwh_per_day": round(kwh_per_day, 2),
            "mesh_cost_per_day": round(kwh_per_day * rate, 2),
            "mesh_cost_per_month": round(kwh_per_day * rate * 30, 2),
            "node_count": len(nodes),
            "nodes_online": sum(1 for n in node_summaries if n.get("status") == "online"),
            "nodes": node_summaries,
        }


def _parse_utc(value: str) -> datetime:
    """Parse UTC timestamps across Python 3.10-3.12."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
