"""Structured JSON logging with a per-run correlation id. File contents are never logged."""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
from datetime import UTC, datetime
from pathlib import Path

_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="-")
_MARK = "_slothdesk_handler"


def set_run_id(value: str) -> None:
    _run_id.set(value)


class _RunIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "run_id": getattr(record, "run_id", "-"),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    logger = logging.getLogger("slothdesk")
    logger.setLevel(level)
    logger.propagate = False
    for h in list(logger.handlers):
        if getattr(h, _MARK, False):
            logger.removeHandler(h)
            h.close()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
        )
    for h in handlers:
        h.setFormatter(JsonFormatter())
        h.addFilter(_RunIdFilter())
        setattr(h, _MARK, True)
        logger.addHandler(h)
