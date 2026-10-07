"""Tests for JSON logs and the log context."""

import contextvars
import json
import logging
import sys
from typing import Any

from shared.logging import (
    JsonFormatter,
    bind_log_context,
    configure_logging,
    current_log_context,
    log_context,
)


def _record(message: str, *, extra: dict[str, Any] | None = None) -> logging.LogRecord:
    return logging.getLogger("test").makeRecord(
        "test", logging.INFO, __file__, 1, message, (), None, extra=extra
    )


def _format(record: logging.LogRecord) -> dict[str, Any]:
    line: dict[str, Any] = json.loads(JsonFormatter().format(record))
    return line


def test_a_log_line_is_json_with_the_context_and_the_extra_fields() -> None:
    record = _record("request finished", extra={"status": 200})

    with log_context(request_id="req-1"):
        bind_log_context(tenant_id="tenant-1")
        line = _format(record)

    assert line["message"] == "request finished"
    assert line["level"] == "INFO"
    assert line["logger"] == "test"
    assert line["request_id"] == "req-1"
    assert line["tenant_id"] == "tenant-1"
    assert line["status"] == 200


def test_the_context_ends_with_its_block() -> None:
    with log_context(request_id="req-1"):
        assert current_log_context() == {"request_id": "req-1"}

    assert current_log_context() == {}


def test_values_bound_in_a_copied_context_are_seen_by_the_whole_request() -> None:
    # Code that runs in a copy of the context (a new task or thread) can still add the tenant.
    with log_context(request_id="req-1"):
        contextvars.copy_context().run(bind_log_context, tenant_id="tenant-1")

        assert current_log_context() == {"request_id": "req-1", "tenant_id": "tenant-1"}


def test_configure_logging_writes_json_and_silences_uvicorn_access_logs() -> None:
    root = logging.getLogger()
    access_logger = logging.getLogger("uvicorn.access")
    saved = (list(root.handlers), root.level, list(access_logger.handlers), access_logger.propagate)
    access_logger.handlers = [logging.NullHandler()]  # what uvicorn's own setup looks like
    try:
        configure_logging("INFO")

        assert [type(handler.formatter) for handler in root.handlers] == [JsonFormatter]
        # uvicorn writes access lines only if the logger has a handler; the API writes its own.
        assert not access_logger.hasHandlers()
    finally:
        # Restore inside the test, while pytest's own log capture handler is still attached.
        root.handlers, root.level, access_logger.handlers, access_logger.propagate = saved


def test_exceptions_are_in_the_log_line() -> None:
    try:
        raise ValueError("something broke")
    except ValueError:
        record = logging.getLogger("test").makeRecord(
            "test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )

    assert "ValueError: something broke" in _format(record)["exception"]
