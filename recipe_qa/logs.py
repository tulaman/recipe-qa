"""One JSON object per log line, with its own level, on stdout.

Railway reads a stderr line as level "error" and reads `level` from a
single-line JSON log, so every line here says what it is. A record may
carry `fields` (a dict, via `extra`): they go to the top level of the
line, which is how the /ask trace stays one queryable object.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import IO, Any

# Railway's level names: debug, info, warn, error.
LEVELS = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warn",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}

# uvicorn installs its own stderr handlers; route them through ours. A
# logger uvicorn left without handlers stays as it is: that is how
# `--no-access-log` silences `uvicorn.access`.
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "level": LEVELS.get(record.levelno, "info"),
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            line.update(fields)
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False, default=str)


def configure(stream: IO[str] | None = None) -> None:
    """Root logger at INFO with one JSON handler; uvicorn's active loggers propagate to it."""
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    for name in UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        if logger.handlers:
            logger.handlers.clear()
            logger.propagate = True
