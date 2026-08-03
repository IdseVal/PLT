"""Structured logging setup.

``docs/architecture.md`` section 2.7 requires structured, levelled logging that never
carries PII from case texts beyond identifiers. Configuration happens once per process,
from :func:`configure_logging`; every module obtains its logger with
:func:`get_logger` and logs contextual fields through the ``extra`` mapping::

    log = get_logger(__name__)
    log.info("fetched candidate", extra={"context": {"ecli": ecli, "connector": "rechtspraak"}})

The ``context`` mapping is rendered as JSON fields, so downstream log processors can filter
on them. Log messages themselves stay short and free of document text.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, Final

from plt.config import LogFormat, Settings

__all__ = [
    "JsonFormatter",
    "configure_logging",
    "get_logger",
]

#: Attributes present on every :class:`logging.LogRecord`; anything else was added by a
#: caller and is therefore worth emitting.
_RESERVED_RECORD_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

_TEXT_FORMAT: Final[str] = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

#: Name stamped on the handler this module installs, so repeated ``create_app()`` calls
#: recognise their own handler instead of stacking a new one on every invocation.
_HANDLER_NAME: Final[str] = "plt.structured"


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects.

    Standard fields are emitted under fixed keys; anything passed through ``extra`` is
    merged in alongside them. Exception information is included as a formatted string so a
    traceback never has to be reassembled from multiple lines.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Return the record serialised as a JSON document.

        Args:
            record: The record to render.

        Returns:
            A single-line JSON string, safe to write to stdout.
        """
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info is not None:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(settings: Settings, *, force: bool = False) -> None:
    """Configure root logging from settings.

    Idempotent: calling it again is a no-op unless ``force`` is set, so an application
    factory invoked repeatedly (as it is in tests) does not stack handlers.

    Args:
        settings: Validated settings supplying the level and the record format.
        force: Reconfigure even if logging was already configured by this function.
    """
    root = logging.getLogger()
    already_configured = any(handler.name == _HANDLER_NAME for handler in root.handlers)
    if already_configured and not force:
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.set_name(_HANDLER_NAME)
    if settings.log_format is LogFormat.JSON:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FORMAT))

    for existing in list(root.handlers):
        root.removeHandler(existing)
        existing.close()
    root.addHandler(handler)
    root.setLevel(settings.log_level)


def get_logger(name: str) -> logging.Logger:
    """Return the module logger for ``name``.

    Args:
        name: Usually ``__name__`` of the calling module.

    Returns:
        A standard library logger; configuration is inherited from the root logger.
    """
    return logging.getLogger(name)
