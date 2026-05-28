"""Tests for PowerMesh database layer."""

import tempfile
from pathlib import Path

import pytest

from src.db import PowerDB


@pytest.fixture
def db(tmp_path):
    """Create a fresh in-memory-like test database."""
    db = PowerDB(tmp_path / "test.db")
    yield db
    db.close()


class TestNodeConfig:
    def test_upsert_and_get(self, db):
        db.upsert_node({"node_id": "n1", "os": "windows", "cpu_tdp_w": 125})
        node = db.get_node("n1")
        assert node is not None
        assert node["node_id"] == "n1"
        assert node["os"] == "windows"
        assert node["cpu_tdp_w"] == 125

    def test_upsert_updates_existing(self, db):
        db.upsert_node({"node_id": "n1", "cpu_tdp_w": 65})
        db.upsert_node({"node_id": "n1", "cpu_tdp_w": 125})
        node = db.get_node("n1")
        assert node["cpu_tdp_w"] == 125

    def test_get_all_nodes(self, db):
        db.upsert_node({"node_id": "alpha"})
        db.upsert_node({"node_id": "beta"})
        nodes = db.get_all_nodes()
        assert len(nodes) == 2
        assert nodes[0]["node_id"] == "alpha"

    def test_get_all_node_ids(self, db):
        db.upsert_node({"node_id": "x"})
        db.upsert_node({"node_id": "y"})
        ids = db.get_all_node_ids()
        assert ids == ["x", "y"]

    def test_get_nonexistent_node(self, db):
        assert db.get_node("nope") is None


class TestReadings:
    def _make_reading(self, node_id="n1", ts="2025-01-01T00:00:00Z", total_w=100):
        return {
            "node_id": node_id,
            "timestamp": ts,
            "cpu_power_w": total_w * 0.3,
            "cpu_util_pct": 45.0,
            "cpu_method": "tdp_estimate",
            "gpu_count": 1,
            "gpu_power_w": total_w * 0.5,
            "gpu_util_pct": 60.0,
            "gpu_vram_used_mb": 4096,
            "gpu_temp_c": 68,
            "ram_used_gb": 16.0,
            "ram_total_gb": 32.0,
            "total_power_w": total_w,
            "psu_efficiency": 0.88,
            "wall_power_w": None,
        }

    def test_insert_and_get_latest(self, db):
        db.insert_reading(self._make_reading(ts="2025-01-01T00:00:00Z", total_w=100))
        db.insert_reading(self._make_reading(ts="2025-01-01T01:00:00Z", total_w=200))
        latest = db.get_latest_reading("n1")
        assert latest is not None
        assert latest["total_power_w"] == 200

    def test_insert_batch(self, db):
        readings = [
            self._make_reading(ts=f"2025-01-01T0{i}:00:00Z", total_w=100 + i * 10)
            for i in range(5)
        ]
        db.insert_readings_batch(readings)
        result = db.get_readings(node_id="n1")
        assert len(result) == 5

    def test_get_latest_readings_multi_node(self, db):
        db.insert_reading(self._make_reading("a", "2025-01-01T00:00:00Z", 100))
        db.insert_reading(self._make_reading("a", "2025-01-01T01:00:00Z", 200))
        db.insert_reading(self._make_reading("b", "2025-01-01T00:30:00Z", 150))
        latest = db.get_latest_readings()
        assert len(latest) == 2
        by_node = {r["node_id"]: r for r in latest}
        assert by_node["a"]["total_power_w"] == 200
        assert by_node["b"]["total_power_w"] == 150

    def test_get_readings_with_time_filter(self, db):
        db.insert_reading(self._make_reading(ts="2025-01-01T00:00:00Z"))
        db.insert_reading(self._make_reading(ts="2025-01-01T06:00:00Z"))
        db.insert_reading(self._make_reading(ts="2025-01-02T00:00:00Z"))
        result = db.get_readings(
            node_id="n1",
            from_ts="2025-01-01T03:00:00Z",
            to_ts="2025-01-01T23:59:59Z",
        )
        assert len(result) == 1

    def test_get_oldest_reading_time(self, db):
        db.insert_reading(self._make_reading(ts="2025-01-03T00:00:00Z"))
        db.insert_reading(self._make_reading(ts="2025-01-01T00:00:00Z"))
        oldest = db.get_oldest_reading_time("n1")
        assert oldest == "2025-01-01T00:00:00Z"

    def test_get_readings_in_range(self, db):
        for h in range(24):
            db.insert_reading(self._make_reading(ts=f"2025-01-01T{h:02d}:00:00Z"))
        result = db.get_readings_in_range("n1", "2025-01-01T10:00:00Z", "2025-01-01T15:00:00Z")
        assert len(result) == 5  # 10, 11, 12, 13, 14 (< 15)

    def test_deduplication(self, db):
        db.insert_reading(self._make_reading(ts="2025-01-01T00:00:00Z", total_w=100))
        db.insert_reading(self._make_reading(ts="2025-01-01T00:00:00Z", total_w=200))
        readings = db.get_readings(node_id="n1")
        assert len(readings) == 1
        assert readings[0]["total_power_w"] == 200  # REPLACE kept latest

    def test_prune_readings(self, db):
        db.insert_reading(self._make_reading(ts="2024-01-01T00:00:00Z"))
        db.insert_reading(self._make_reading(ts="2025-06-01T00:00:00Z"))
        pruned = db.prune_readings("2025-01-01T00:00:00Z")
        assert pruned == 1
        remaining = db.get_readings(node_id="n1")
        assert len(remaining) == 1


class TestAggregates:
    def test_insert_and_get(self, db):
        agg = {
            "node_id": "n1",
            "period_start": "2025-01-01T00:00:00Z",
            "period_end": "2025-01-01T01:00:00Z",
            "period_type": "hourly",
            "avg_power_w": 150.5,
            "max_power_w": 200,
            "min_power_w": 100,
            "energy_wh": 150.5,
            "cost": 0.018,
            "reading_count": 60,
        }
        db.insert_aggregate(agg)
        result = db.get_aggregates(node_id="n1", period_type="hourly")
        assert len(result) == 1
        assert result[0]["avg_power_w"] == 150.5
        assert result[0]["energy_wh"] == 150.5

    def test_get_latest_aggregate(self, db):
        for i in range(3):
            db.insert_aggregate({
                "node_id": "n1",
                "period_start": f"2025-01-01T{i:02d}:00:00Z",
                "period_end": f"2025-01-01T{i+1:02d}:00:00Z",
                "period_type": "hourly",
                "avg_power_w": 100 + i * 10,
                "energy_wh": 100 + i * 10,
                "reading_count": 60,
            })
        latest = db.get_latest_aggregate("n1", "hourly")
        assert latest is not None
        assert latest["period_start"] == "2025-01-01T02:00:00Z"
        assert latest["avg_power_w"] == 120

    def test_upsert_aggregate(self, db):
        agg = {
            "node_id": "n1",
            "period_start": "2025-01-01T00:00:00Z",
            "period_type": "hourly",
            "avg_power_w": 100,
            "energy_wh": 100,
        }
        db.insert_aggregate(agg)
        agg["avg_power_w"] = 150
        agg["energy_wh"] = 150
        db.insert_aggregate(agg)
        result = db.get_aggregates(node_id="n1", period_type="hourly")
        assert len(result) == 1
        assert result[0]["avg_power_w"] == 150
