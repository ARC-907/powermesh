from pathlib import Path

from src.config import load_mesh_config, load_node_config


def test_node_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("POWERMESH_NODE_ID", "test-node")
    monkeypatch.setenv("POWERMESH_DATA_DIR", str(tmp_path / "data"))
    config = load_node_config(config={"node_id": "from-file", "data_dir": "data"})
    assert config["node_id"] == "test-node"
    assert Path(config["data_dir"]) == tmp_path / "data"


def test_mesh_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("POWERMESH_COLLECTOR_PORT", "9444")
    monkeypatch.setenv("POWERMESH_DATA_DIR", str(tmp_path / "collector"))
    config = load_mesh_config(config={"port": 8430, "data_dir": "data"})
    assert config["port"] == 9444
    assert Path(config["data_dir"]) == tmp_path / "collector"


def test_nested_smart_plug_merge(tmp_path):
    config_path = tmp_path / "node.yaml"
    config_path.write_text("smart_plug:\n  enabled: true\n  ip: 192.0.2.10\n", encoding="utf-8")
    config = load_node_config(config_path=config_path)
    assert config["smart_plug"]["enabled"] is True
    assert config["smart_plug"]["type"] == "kasa"
    assert config["smart_plug"]["ip"] == "192.0.2.10"


def test_empty_mesh_collection_keys_normalize(tmp_path):
    config_path = tmp_path / "mesh.yaml"
    config_path.write_text("expected_nodes:\nauth_tokens:\n", encoding="utf-8")
    config = load_mesh_config(config_path=config_path)
    assert config["expected_nodes"] == []
    assert config["auth_tokens"] == {}
