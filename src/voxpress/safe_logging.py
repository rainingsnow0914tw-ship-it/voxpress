"""Metadata-only rotating logs for a tool that handles private dictation."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from typing import Any

from voxpress.config import safe_model_label

_REDACTED = "[redacted]"
_SENSITIVE_MARKERS = (
    "text",
    "transcript",
    "clipboard",
    "prompt",
    "token",
    "secret",
    "password",
    "path",
    "title",
)
_SAFE_STRING_FIELDS = {
    "accepted",
    "app",
    "commit",
    "component",
    "device",
    "disposition",
    "error",
    "error_code",
    "event_type",
    "hotkey",
    "key",
    "kind",
    "method",
    "model",
    "note",
    "outcome",
    "reason",
    "state",
    "status",
    "version",
}


def default_log_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local"))
    return base / "VoxPress" / "logs"


def _safe_value(name: str, value: Any) -> Any:
    lowered = name.casefold()
    if any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return _REDACTED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if name == "model":
        return safe_model_label(value)
    if isinstance(value, str):
        return value[:160] if name in _SAFE_STRING_FIELDS else _REDACTED
    if isinstance(value, (list, tuple)) and all(
        isinstance(item, (bool, int, float)) for item in value
    ):
        return list(value)
    return _REDACTED


class SafeLogger:
    def __init__(
        self,
        logger: logging.Logger,
        *,
        diagnostics: bool = False,
        listener: QueueListener | None = None,
        sinks: tuple[logging.Handler, ...] = (),
    ) -> None:
        self._logger = logger
        self._diagnostics = diagnostics
        self._listener = listener
        self._sinks = sinks
        self._close_lock = threading.Lock()
        self._closed = False

    def _emit(self, level: int, event: str, fields: dict[str, Any]) -> None:
        if self._closed:
            return
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **{name: _safe_value(name, value) for name, value in fields.items()},
        }
        self._logger.log(level, json.dumps(record, ensure_ascii=False, sort_keys=True))

    def info(self, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, event, fields)

    def warn(self, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, event, fields)

    warning = warn

    def error(self, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event, fields)

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, fields)

    def diagnostic(self, event: str, **fields: Any) -> None:
        if self._diagnostics:
            self._emit(logging.DEBUG, event, fields)

    def close(self) -> None:
        """Drain queued records and close sinks. Safe to call more than once."""

        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            listener, self._listener = self._listener, None
        if listener is not None:
            listener.stop()
        for handler in tuple(self._logger.handlers):
            self._logger.removeHandler(handler)
            handler.close()
        for sink in self._sinks:
            sink.close()


def setup_logging(
    *,
    log_dir: Path | None = None,
    console: bool = False,
    diagnostics: bool = False,
) -> SafeLogger:
    target = default_log_dir() if log_dir is None else Path(log_dir)
    target.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"voxpress.{id(target)}")
    logger.setLevel(logging.DEBUG if diagnostics else logging.INFO)
    logger.propagate = False
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter("%(message)s")
    file_handler = RotatingFileHandler(
        target / "voxpress.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    sinks: list[logging.Handler] = [file_handler]
    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        sinks.append(stream)

    log_queue: queue.Queue[logging.LogRecord] = queue.Queue()
    logger.addHandler(QueueHandler(log_queue))
    listener = QueueListener(log_queue, *sinks, respect_handler_level=True)
    listener.start()
    return SafeLogger(
        logger,
        diagnostics=diagnostics,
        listener=listener,
        sinks=tuple(sinks),
    )
