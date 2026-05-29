"""Tests for CORS allowlist behavior and loopback-gating of /api/refresh.

These exercise the live HTTP server on 127.0.0.1 so the request-handler
codepath is the same one a real client would hit.
"""

from __future__ import annotations

import socket
import threading
import time
import urllib.request
import urllib.error
from typing import Any

import pytest

from src.collector import CollectorHandler, CollectorServer
from src.aggregator import Aggregator
from src.api import PowerAPI
from src.db import PowerDB


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Harness:
    """Spin up a real CollectorServer on 127.0.0.1 with a given config dict."""

    def __init__(self, tmp_path, extra_config: dict[str, Any] | None = None) -> None:
        self.port = _free_port()
        self.db = PowerDB(tmp_path / "harness.db")
        self.db.upsert_node({"node_id": "n1"})
        self.config: dict[str, Any] = {
            "host": "127.0.0.1",
            "port": self.port,
            "auth_tokens": {},
            "cors_allow_origins": [],
        }
        if extra_config:
            self.config.update(extra_config)
        aggregator = Aggregator(self.db)
        self.api = PowerAPI(self.db, aggregator, config=self.config)
        self.server = CollectorServer(("127.0.0.1", self.port), CollectorHandler)
        self.server.db = self.db
        self.server.config = self.config
        self.server.api = self.api
        self.server.aggregator = aggregator
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        # tiny settle
        time.sleep(0.05)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.db.close()


@pytest.fixture()
def harness(tmp_path):
    h = _Harness(tmp_path)
    yield h
    h.close()


# ───── CORS allowlist ────────────────────────────────────────────────

def test_no_cors_header_by_default(harness: _Harness) -> None:
    req = urllib.request.Request(harness.url("/api/health"))
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        assert resp.headers.get("Access-Control-Allow-Origin") is None


def test_no_cors_when_origin_not_in_allowlist(tmp_path) -> None:
    h = _Harness(tmp_path, extra_config={"cors_allow_origins": ["https://allowed.example.com"]})
    try:
        req = urllib.request.Request(
            h.url("/api/health"),
            headers={"Origin": "https://evil.example.com"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.headers.get("Access-Control-Allow-Origin") is None
    finally:
        h.close()


def test_cors_echoes_allowlisted_origin(tmp_path) -> None:
    h = _Harness(tmp_path, extra_config={"cors_allow_origins": ["https://allowed.example.com"]})
    try:
        req = urllib.request.Request(
            h.url("/api/health"),
            headers={"Origin": "https://allowed.example.com"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.headers.get("Access-Control-Allow-Origin") == "https://allowed.example.com"
    finally:
        h.close()


def test_cors_wildcard_only_when_opted_in(tmp_path) -> None:
    h = _Harness(tmp_path, extra_config={"cors_allow_origins": ["*"]})
    try:
        req = urllib.request.Request(h.url("/api/health"), headers={"Origin": "https://anything.example"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"
    finally:
        h.close()


# ───── /api/refresh loopback gating ─────────────────────────────────

def test_refresh_succeeds_from_loopback(harness: _Harness) -> None:
    # POST from the test process — client connects from 127.0.0.1, so the
    # loopback gate lets it through.
    req = urllib.request.Request(
        harness.url("/api/refresh"),
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200


def test_refresh_blocked_when_client_is_not_loopback(harness: _Harness, monkeypatch) -> None:
    # Simulate a non-loopback caller by patching _is_loopback_client to return
    # False. This is the cheapest way to exercise the gate without binding the
    # server to a real LAN interface.
    monkeypatch.setattr(CollectorHandler, "_is_loopback_client", lambda self: False)
    req = urllib.request.Request(
        harness.url("/api/refresh"),
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 403


def test_settings_post_still_blocked_from_non_loopback(harness: _Harness, monkeypatch) -> None:
    monkeypatch.setattr(CollectorHandler, "_is_loopback_client", lambda self: False)
    req = urllib.request.Request(
        harness.url("/api/settings"),
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 403
