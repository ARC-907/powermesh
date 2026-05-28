"""Logging setup helpers with token redaction."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import ensure_dir


class RedactingFilter(logging.Filter):
    """Mask common secret labels before records hit console or file logs."""

    _MARKERS = ("auth_token", "auth_tokens", "token", "password", "secret")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        lowered = message.lower()
        if any(marker in lowered for marker in self._MARKERS):
            record.msg = "[redacted sensitive log message]"
            record.args = ()
        return True


def setup_logging(component: str, data_dir: str | Path, level: str = "INFO") -> Path:
    """Configure console and rotating file logging. Returns the log path."""
    log_dir = ensure_dir(Path(data_dir) / "logs")
    log_path = log_dir / f"{component}.log"
    numeric_level = getattr(logging, str(level).upper(), logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    redactor = RedactingFilter()

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(numeric_level)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(redactor)
    root.addHandler(stream_handler)

    file_handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redactor)
    root.addHandler(file_handler)

    return log_path
