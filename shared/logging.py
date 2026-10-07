"""Structured logs: one JSON object per line, easy to search and to send to a log system.

A *log context* holds values like the request ID and the tenant ID. They are added to every
log line written while one request (or one worker job) is handled.
"""

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# One dict per request or job. Later code in the same request adds keys to this same dict,
# so copies of the context (new tasks, threads) still share the values, e.g. the tenant ID.
_log_context: ContextVar[dict[str, str] | None] = ContextVar("log_context", default=None)

# Attributes that every log record has. Any other attribute came from `extra={...}`.
_STANDARD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


@contextmanager
def log_context(**values: str) -> Iterator[None]:
    """Start a new log context: `with log_context(request_id="..."):`."""
    token = _log_context.set(dict(values))
    try:
        yield
    finally:
        _log_context.reset(token)


def bind_log_context(**values: str) -> None:
    """Add values to the current log context (e.g. the tenant, once we know who is calling)."""
    current = _log_context.get()
    if current is None:
        _log_context.set(dict(values))
    else:
        current.update(values)


def current_log_context() -> dict[str, str]:
    """A copy of the values in the current log context."""
    return dict(_log_context.get() or {})


class JsonFormatter(logging.Formatter):
    """Format a log record as one line of JSON, with the log context and `extra` fields."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **current_log_context(),
        }
        entry.update(
            (key, value) for key, value in vars(record).items() if key not in _STANDARD_ATTRIBUTES
        )
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str) -> None:
    """Send all logs (ours, uvicorn's, alembic's) to stdout as JSON lines."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # uvicorn installs its own handlers. Send its logs through ours instead...
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # ...except its access log: the API writes its own line (JSON, with the request ID).
    # uvicorn only writes access lines when this logger has a handler somewhere.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
    # httpx (used by the Qdrant client) logs every HTTP request at INFO: too much.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
