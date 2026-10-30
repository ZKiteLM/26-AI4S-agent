"""Small, shared diagnostic stream for API, database and worker activity."""

import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from functools import lru_cache

from redis import Redis
from sqlalchemy import event

from .config import settings

STREAM = "research:diagnostic_events"
_disabled_until = 0.0


@lru_cache
def client() -> Redis:
    return Redis.from_url(settings().redis_url, socket_connect_timeout=0.3, socket_timeout=0.3)


def record(source: str, level: str, message: str, **details: object) -> None:
    """Do not let unavailable diagnostics interrupt normal requests or jobs."""
    global _disabled_until
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    if settings().deployment_mode == "cloud" and not settings().debug_console_key:
        return
    if time.monotonic() < _disabled_until:
        return
    fields = {
        "time": datetime.now(UTC).isoformat(),
        "source": source,
        "level": level,
        "message": message[:500],
        "details": json.dumps(details, ensure_ascii=False, default=str)[:1500],
    }
    try:
        client().xadd(STREAM, fields, maxlen=3000, approximate=True)
    except Exception:
        _disabled_until = time.monotonic() + 5
        return


class DiagnosticLogHandler(logging.Handler):
    def emit(self, entry: logging.LogRecord) -> None:
        message = entry.getMessage()
        message = re.sub(r"Bearer\s+[\w.\-]+", "Bearer [redacted]", message, flags=re.I)
        message = re.sub(r"(?i)(api[_-]?key|password)=\S+", r"\1=[redacted]", message)
        record("backend", entry.levelname, message, logger=entry.name)


def install_python_logging() -> None:
    package_logger = logging.getLogger("services.api")
    if not any(isinstance(handler, DiagnosticLogHandler) for handler in package_logger.handlers):
        package_logger.addHandler(DiagnosticLogHandler())
        package_logger.setLevel(logging.INFO)


def recent(limit: int = 150) -> list[dict]:
    rows = client().xrevrange(STREAM, count=limit)
    return [
        {
            "id": key.decode(),
            **{name.decode(): value.decode() for name, value in fields.items()},
        }
        for key, fields in rows
    ]


def install_db_observer(engine) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def before(_conn, _cursor, _statement, _parameters, context, _executemany):
        context._diagnostic_started = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def after(_conn, _cursor, statement, _parameters, context, _executemany):
        if context.execution_options.get("skip_diagnostic_log"):
            return
        elapsed = round((time.perf_counter() - context._diagnostic_started) * 1000, 1)
        operation = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else "SQL"
        match = re.search(r"\b(?:FROM|INTO|UPDATE|JOIN)\s+([\w.]+)", statement, re.I)
        table = match.group(1) if match else ""
        record(
            "database",
            "WARN" if elapsed >= 100 else "INFO",
            f"{operation} {table}".strip(),
            duration_ms=elapsed,
        )

    @event.listens_for(engine, "handle_error")
    def on_error(context):
        record("database", "ERROR", type(context.original_exception).__name__)
