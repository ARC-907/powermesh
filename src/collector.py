"""PowerMesh Collector — central HTTP server that ingests readings from agents."""

# pylint: disable=broad-exception-caught,redefined-builtin

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import signal
import sys
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qsl, parse_qs

from .aggregator import Aggregator
from .api import ApiResponse, PowerAPI
from .config import load_mesh_config
from .db import PowerDB
from .logging_utils import setup_logging

log = logging.getLogger("powermesh.collector")

# Hostnames treated as loopback for the bind-safety check.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class InsecureBindError(RuntimeError):
    """Raised when the collector is asked to bind a non-loopback address unsafely."""


def _is_loopback_bind(host: str) -> bool:
    """Return True if the configured host is unambiguously loopback."""
    if not host:
        return False
    normalized = host.strip().lower()
    if normalized in LOOPBACK_HOSTS:
        return True
    # IPv4 loopback /8: 127.x.x.x
    if normalized.startswith("127."):
        return True
    return False


def _enforce_bind_safety(host: str, auth_tokens: dict[str, Any] | None, public: bool) -> None:
    """Refuse to start when a non-loopback bind would expose an unauthenticated collector.

    Rules:
      * Loopback binds (127.x, ::1, localhost) are always allowed.
      * Non-loopback binds require BOTH an explicit public opt-in AND a
        non-empty auth_tokens mapping. The opt-in is wired to the --public
        CLI flag (see main()); callers embedding run_collector() pass public=True.
      * If either is missing, raise InsecureBindError. The CLI converts this to
        SystemExit(2); embedding callers (e.g. tests) can catch it directly.
    """
    if _is_loopback_bind(host):
        return

    if not public:
        raise InsecureBindError(
            f"ERROR: Non-loopback bind ({host}) requires explicit opt-in.\n"
            f"See SECURITY.md and README.md \"Public/LAN deployment\" for how to set this up.\n"
            f"Refusing to start."
        )

    if not auth_tokens:
        raise InsecureBindError(
            f"ERROR: Non-loopback bind ({host}) requires auth_tokens to be configured.\n"
            f"See SECURITY.md and README.md \"Public/LAN deployment\" for how to set this up.\n"
            f"Refusing to start."
        )

class CollectorHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the power collector."""

    @property
    def app_server(self) -> "CollectorServer":
        return cast(CollectorServer, self.server)

    def do_GET(self) -> None:
        api: PowerAPI = self.app_server.api
        path = self.path.split("?", 1)
        route = path[0]
        query_str = path[1] if len(path) > 1 else ""
        params = _parse_query(query_str)

        handler = api.route_get(route)
        if handler:
            try:
                result = handler(params)
                self._send_result(result)
            except Exception as e:
                log.error("Handler error for %s: %s", route, e, exc_info=True)
                self._send_json(500, {"error": str(e)})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        route = self.path.split("?", 1)[0]

        if route == "/api/power/ingest":
            try:
                self._handle_ingest()
            except Exception as e:
                log.error("Ingest error: %s", e, exc_info=True)
                try:
                    self._send_json(500, {"error": "internal server error"})
                except Exception:
                    pass
        elif self.app_server.api.route_post(route):
            self._handle_api_post(route)
        else:
            self._send_json(404, {"error": "not found"})

    # Mutating POST routes that may only be invoked from the loopback interface.
    # These are dashboard-driven operations; cross-origin invocation from a
    # malicious page on the same network would otherwise trigger writes.
    LOOPBACK_ONLY_POST_ROUTES = frozenset({"/api/settings", "/api/refresh"})

    def _handle_api_post(self, route: str) -> None:
        if route in self.LOOPBACK_ONLY_POST_ROUTES and not self._is_loopback_client():
            self._send_json(403, {"error": f"{route} is restricted to localhost"})
            return
        handler = self.app_server.api.route_post(route)
        if not handler:
            self._send_json(404, {"error": "not found"})
            return
        payload = self._read_payload()
        try:
            self._send_result(handler(payload))
        except Exception as e:
            log.error("POST handler error for %s: %s", route, e, exc_info=True)
            self._send_json(500, {"error": str(e)})

    def _read_payload(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            return {}
        body = self.rfile.read(content_length).decode("utf-8")
        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return json.loads(body or "{}")
        form = parse_qs(body, keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in form.items()}

    def _is_loopback_client(self) -> bool:
        host = self.client_address[0]
        return host == "::1" or host.startswith("127.")

    def _handle_ingest(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json(400, {"error": "empty body"})
            return
        if content_length > 10 * 1024 * 1024:  # 10 MB limit
            self._send_json(413, {"error": "payload too large"})
            return

        body = self.rfile.read(content_length)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid JSON"})
            return

        node_id = payload.get("node_id", "")
        if not node_id:
            self._send_json(400, {"error": "missing node_id"})
            return

        # HMAC verification
        auth_tokens: dict = self.app_server.config.get("auth_tokens", {})
        token = auth_tokens.get(node_id, auth_tokens.get("*", ""))
        if token:
            sig_header = self.headers.get("X-PowerMesh-Signature", "")
            expected_body = json.dumps(payload, sort_keys=True)
            expected_sig = hmac.new(
                token.encode(), expected_body.encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(sig_header, expected_sig):
                self._send_json(403, {"error": "invalid signature"})
                return

        readings = payload.get("readings", [])
        if not readings:
            self._send_json(400, {"error": "no readings"})
            return

        db: PowerDB = self.app_server.db
        accepted = 0
        for reading in readings:
            reading["node_id"] = node_id
            try:
                db.insert_reading(reading)
                accepted += 1
            except Exception as e:
                log.warning("Failed to insert reading: %s", e)

        # Update node last_seen
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.upsert_node({"node_id": node_id, "last_seen": now})

        log.info("Ingested %d/%d readings from %s", accepted, len(readings), node_id)
        self._send_json(200, {"accepted": accepted, "total": len(readings)})

    def _cors_origin_for_request(self) -> str | None:
        """Return the value to echo in Access-Control-Allow-Origin, or None.

        Behavior:
          * If the configured allowlist is empty, return None (no CORS header
            emitted — same-origin only).
          * If the request's Origin header is in the allowlist, echo it back.
          * Otherwise return None.

        Wildcard "*" is honored ONLY if the operator explicitly placed it in
        the allowlist; the default config does not.
        """
        allow = self.app_server.config.get("cors_allow_origins") or []
        if not allow:
            return None
        origin = self.headers.get("Origin", "")
        if "*" in allow:
            return "*"
        if origin and origin in allow:
            return origin
        return None

    def _send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        cors = self._cors_origin_for_request()
        if cors is not None:
            self.send_header("Access-Control-Allow-Origin", cors)
        self.end_headers()
        self.wfile.write(body)

    def _send_result(self, result: Any) -> None:
        if isinstance(result, ApiResponse):
            self._send_response(result.status, result.body, result.content_type, result.headers)
        elif isinstance(result, str):
            self._send_response(200, result, content_type="text/html")
        else:
            self._send_json(200, result)

    def _send_response(
        self,
        status: int,
        body: str,
        content_type: str = "text/plain",
        headers: dict[str, str] | None = None,
    ) -> None:
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        cors = self._cors_origin_for_request()
        if cors is not None:
            self.send_header("Access-Control-Allow-Origin", cors)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        log.debug(format, *args)


class CollectorServer(HTTPServer):
    """Extended HTTPServer with db, config, api, and aggregator."""
    db: PowerDB
    config: dict[str, Any]
    api: PowerAPI
    aggregator: Aggregator


def _parse_query(query_str: str) -> dict[str, str]:
    return dict(parse_qsl(query_str, keep_blank_values=True))


def run_collector(
    config_path: str | Path | None = "config/mesh.yaml",
    config: dict[str, Any] | None = None,
    app_info: dict[str, Any] | None = None,
    install_signal_handlers: bool = True,
    public: bool = False,
) -> None:
    config = load_mesh_config(config_path=config_path, config=config)

    host = config.get("host", "127.0.0.1")
    auth_tokens = config.get("auth_tokens") or {}
    _enforce_bind_safety(host, auth_tokens, public)

    data_dir = Path(config["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    db = PowerDB(data_dir / "collector.db")

    # Register expected nodes
    for node_spec in config.get("expected_nodes") or []:
        if isinstance(node_spec, str):
            db.upsert_node({"node_id": node_spec})
        elif isinstance(node_spec, dict):
            db.upsert_node(node_spec)

    aggregator = Aggregator(
        db,
        default_cost_per_kwh=config.get("cost_per_kwh_default", 0.12),
        retention_days=config.get("retention_days", 30),
    )
    api = PowerAPI(db, aggregator, config=config, app_info=app_info)

    port = config["port"]
    server = CollectorServer((host, port), CollectorHandler)
    server.db = db
    server.config = config
    server.api = api
    server.aggregator = aggregator

    # Background aggregation thread
    agg_interval = config.get("aggregation_interval_m", 60) * 60
    _stop_event = threading.Event()

    def _aggregation_loop() -> None:
        while not _stop_event.is_set():
            try:
                aggregator.run_hourly()
                aggregator.run_daily()
                aggregator.prune_old_readings()
            except Exception as e:
                log.error("Aggregation cycle failed: %s", e)
            _stop_event.wait(agg_interval)

    agg_thread = threading.Thread(target=_aggregation_loop, daemon=True)
    agg_thread.start()

    def _shutdown(_sig: int, _frame: Any) -> None:
        log.info("Shutdown signal received")
        _stop_event.set()
        server.shutdown()

    if install_signal_handlers:
        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

    log.info("PowerMesh collector listening on %s:%d", host, port)
    try:
        server.serve_forever()
    finally:
        db.close()
        log.info("Collector stopped")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="powermesh-collector",
        description=(
            "Run the PowerMesh collector. The collector binds to loopback by "
            "default. To bind a non-loopback address (LAN / Tailscale / public), "
            "pass --public AND configure non-empty auth_tokens in mesh.yaml. "
            "Both are required: --public alone with an empty auth_tokens map "
            "will refuse to start."
        ),
    )
    parser.add_argument(
        "config",
        nargs="?",
        default="config/mesh.yaml",
        help="Path to mesh.yaml (default: config/mesh.yaml).",
    )
    parser.add_argument(
        "--public",
        action="store_true",
        help=(
            "Explicit opt-in for non-loopback bind. Required (together with a "
            "populated auth_tokens map) before the collector will bind anything "
            "other than 127.0.0.1 / ::1 / localhost."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    config_path = args.config
    config = load_mesh_config(config_path=config_path)
    setup_logging("collector", config["data_dir"], config.get("log_level", "INFO"))
    try:
        run_collector(
            config_path=config_path,
            app_info={"edition": "Full", "version": "0.1.0"},
            public=args.public,
        )
    except InsecureBindError as exc:
        # Print to stderr in addition to logging so install scripts see the reason.
        sys.stderr.write(f"{exc}\n")
        log.error("%s", exc)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
