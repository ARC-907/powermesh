"""Tests for the aggregation engine."""

import pytest

from src.db import PowerDB
from src.aggregator import Aggregator


@pytest.fixture
def db(tmp_path):
    db = PowerDB(tmp_path / "test.db")
    db.upsert_node({"node_id": "n1", "cost_per_kwh": 0.12, "currency": "USD"})
    yield db
    db.close()


def _reading(node_id, ts, total_w):
    return {
        "node_id": node_id,
        "timestamp": ts,
        "cpu_power_w": total_w * 0.4,
        "cpu_util_pct": 50.0,
        "cpu_method": "tdp_estimate",
        "gpu_count": 1,
        "gpu_power_w": total_w * 0.5,
        "gpu_util_pct": 70.0,
        "gpu_vram_used_mb": 4096,
        "gpu_temp_c": 65,
        "ram_used_gb": 16.0,
        "ram_total_gb": 32.0,
        "total_power_w": total_w,
        "psu_efficiency": 0.90,
        "wall_power_w": total_w / 0.90,
    }


class TestComputeAggregate:
    def test_single_hour(self, db):
        # Insert 60 one-minute samples across one hour
        for m in range(60):
            db.insert_reading(_reading("n1", f"2025-01-01T10:{m:02d}:00Z", 100 + m))

        agg = Aggregator(db)
        agg.run_hourly()

        result = db.get_aggregates(node_id="n1", period_type="hourly")
        assert len(result) >= 1

        first = result[0]
        assert first["avg_power_w"] > 0
        assert first["energy_wh"] > 0
        assert first["reading_count"] == 60

    def test_mesh_summary(self, db):
        db.upsert_node({"node_id": "n2", "cost_per_kwh": 0.10})
        db.insert_reading(_reading("n1", "2025-01-01T10:00:00Z", 100))
        db.insert_reading(_reading("n2", "2025-01-01T10:00:00Z", 200))

        agg = Aggregator(db)
        summary = agg.get_mesh_summary()

        assert summary["mesh_total_power_w"] == 300
        assert len(summary["nodes"]) == 2

    def test_prune_old_readings(self, db):
        db.insert_reading(_reading("n1", "2024-01-01T00:00:00Z", 100))
        # Use a timestamp far in the future so it survives pruning
        db.insert_reading(_reading("n1", "2099-01-01T00:00:00Z", 200))

        agg = Aggregator(db, retention_days=30)
        agg.prune_old_readings()

        readings = db.get_readings(node_id="n1")
        assert len(readings) == 1
        assert readings[0]["total_power_w"] == 200
