from src.aggregator import Aggregator
from src.api import ApiResponse, PowerAPI
from src.db import PowerDB


def _reading(node_id="n1", ts="2026-05-26T12:00:00Z", total_w=120):
    return {
        "node_id": node_id,
        "timestamp": ts,
        "cpu_power_w": total_w * 0.4,
        "cpu_util_pct": 25.0,
        "cpu_method": "tdp_estimate",
        "gpu_count": 0,
        "gpu_power_w": 0,
        "gpu_util_pct": 0,
        "ram_used_gb": 8,
        "ram_total_gb": 16,
        "total_power_w": total_w,
        "psu_efficiency": 0.88,
    }


def test_export_csv(tmp_path):
    db = PowerDB(tmp_path / "test.db")
    db.upsert_node({"node_id": "n1"})
    db.insert_reading(_reading())
    api = PowerAPI(db, Aggregator(db))

    handler = api.route_get("/api/export")
    assert handler is not None
    response = handler({"format": "csv", "range": "30d"})
    assert isinstance(response, ApiResponse)
    assert response.content_type == "text/csv"
    assert "node_id" in response.body
    assert "n1" in response.body
    db.close()


def test_refresh_route(tmp_path):
    db = PowerDB(tmp_path / "test.db")
    db.upsert_node({"node_id": "n1"})
    api = PowerAPI(db, Aggregator(db))
    handler = api.route_post("/api/refresh")
    assert handler is not None
    result = handler({})
    assert result["status"] == "ok"
    db.close()
