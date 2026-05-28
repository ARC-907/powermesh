"""Single-process PowerMesh Lite launcher."""

from __future__ import annotations

import logging
import signal
import threading
import webbrowser
from typing import Any

from .agent import PowerAgent
from .collector import run_collector
from .config import load_mesh_config, load_node_config
from .logging_utils import setup_logging
from .paths import ensure_dir, user_data_dir

log = logging.getLogger("powermesh.lite")


def main() -> None:
    port = 8430
    base_dir = ensure_dir(user_data_dir() / "lite")
    collector_data = ensure_dir(base_dir / "collector")
    agent_data = ensure_dir(base_dir / "agent")

    mesh_config = load_mesh_config(config={
        "host": "127.0.0.1",
        "port": port,
        "data_dir": str(collector_data),
        "auth_tokens": {},
        "aggregation_interval_m": 5,
        "retention_days": 7,
    })
    node_config = load_node_config(config={
        "collector_url": f"http://127.0.0.1:{port}",
        "data_dir": str(agent_data),
        "collection_interval_s": 10,
        "push_batch_size": 1,
        "smart_plug": {"enabled": False, "type": "kasa", "ip": ""},
    })

    setup_logging("lite", base_dir, mesh_config.get("log_level", "INFO"))
    agent = PowerAgent(config=node_config)

    def stop(_sig: int | None = None, _frame: Any | None = None) -> None:
        log.info("Lite shutdown requested")
        agent.stop()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    agent_thread = threading.Thread(target=agent.run, daemon=True)
    agent_thread.start()
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    log.info("PowerMesh Lite starting at http://127.0.0.1:%d/", port)
    run_collector(
        config=mesh_config,
        config_path=None,
        app_info={"edition": "Lite", "version": "0.1.0"},
        install_signal_handlers=False,
    )


if __name__ == "__main__":
    main()
