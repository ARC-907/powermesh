"""Tests for the loopback-bind safety gate in src.collector.

These tests cover the rules implemented by `_enforce_bind_safety` and the
`run_collector(public=...)` parameter:

  * Loopback bind (127.x, ::1, localhost) is always allowed.
  * Non-loopback bind requires BOTH explicit opt-in (public=True / --public)
    AND non-empty auth_tokens.
  * Missing either raises InsecureBindError (which main() converts to
    SystemExit(2)).
"""

from __future__ import annotations

import pytest

from src.collector import (
    InsecureBindError,
    _enforce_bind_safety,
    _is_loopback_bind,
    main,
    run_collector,
)


# ---------- _is_loopback_bind ---------------------------------------------------

@pytest.mark.parametrize("host", ["127.0.0.1", "127.1.2.3", "localhost", "::1", "LocalHost", " 127.0.0.1 "])
def test_loopback_hosts_are_recognised(host: str) -> None:
    assert _is_loopback_bind(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5", "192.168.1.10", "100.64.0.2", "example.com", ""])
def test_non_loopback_hosts_are_rejected(host: str) -> None:
    assert _is_loopback_bind(host) is False


# ---------- _enforce_bind_safety -----------------------------------------------

def test_loopback_bind_is_always_allowed_even_without_auth() -> None:
    # No tokens, no opt-in, loopback → allowed.
    _enforce_bind_safety("127.0.0.1", {}, public=False)
    _enforce_bind_safety("::1", None, public=False)
    _enforce_bind_safety("localhost", {}, public=True)


def test_non_loopback_without_opt_in_refuses() -> None:
    with pytest.raises(InsecureBindError) as exc:
        _enforce_bind_safety("0.0.0.0", {"*": "secret"}, public=False)
    assert "Refusing to start" in str(exc.value)


def test_non_loopback_with_opt_in_but_empty_auth_refuses() -> None:
    with pytest.raises(InsecureBindError) as exc:
        _enforce_bind_safety("0.0.0.0", {}, public=True)
    assert "auth_tokens" in str(exc.value)
    assert "Refusing to start" in str(exc.value)


def test_non_loopback_with_opt_in_and_none_auth_refuses() -> None:
    with pytest.raises(InsecureBindError):
        _enforce_bind_safety("192.168.1.10", None, public=True)


def test_non_loopback_with_opt_in_and_auth_is_allowed() -> None:
    _enforce_bind_safety("0.0.0.0", {"*": "shared-secret"}, public=True)
    _enforce_bind_safety("100.64.0.2", {"desktop-01": "tok"}, public=True)


# ---------- run_collector integration ------------------------------------------

def test_run_collector_refuses_non_loopback_without_opt_in(tmp_path, monkeypatch) -> None:
    # Point data_dir into tmp_path so we don't touch real user dirs.
    monkeypatch.setenv("POWERMESH_DATA_DIR", str(tmp_path / "data"))
    inline = {
        "host": "0.0.0.0",
        "port": 18430,
        "auth_tokens": {"*": "shared-secret"},
        "data_dir": str(tmp_path / "data"),
    }
    with pytest.raises(InsecureBindError):
        run_collector(config_path=None, config=inline, public=False)


def test_run_collector_refuses_non_loopback_with_opt_in_but_no_tokens(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("POWERMESH_DATA_DIR", str(tmp_path / "data"))
    inline = {
        "host": "0.0.0.0",
        "port": 18431,
        "auth_tokens": {},
        "data_dir": str(tmp_path / "data"),
    }
    with pytest.raises(InsecureBindError):
        run_collector(config_path=None, config=inline, public=True)


# ---------- main() CLI exit code ------------------------------------------------

def test_main_exits_with_code_2_on_insecure_bind(tmp_path, monkeypatch) -> None:
    # Write a config that asks for 0.0.0.0 with no tokens, then invoke main()
    # without --public. main() should SystemExit(2).
    cfg = tmp_path / "mesh.yaml"
    cfg.write_text(
        "host: 0.0.0.0\nport: 18432\nauth_tokens: {}\ndata_dir: '"
        + str(tmp_path / "data").replace("\\", "/") + "'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("POWERMESH_DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(SystemExit) as exc:
        main([str(cfg)])
    assert exc.value.code == 2


def test_main_help_mentions_public_flag(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "--public" in captured.out
    assert "auth_tokens" in captured.out
