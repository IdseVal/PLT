"""Tests for :mod:`plt.utils.logging`."""

from __future__ import annotations

import json
import logging

from plt.config import LogFormat
from plt.utils.logging import JsonFormatter, configure_logging, get_logger
from tests.conftest import build_settings


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="plt.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="fetched %s",
        args=("ECLI:NL:HR:2024:1",),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_one_line_of_json() -> None:
    rendered = JsonFormatter().format(_record(connector="rechtspraak"))

    assert "\n" not in rendered
    payload = json.loads(rendered)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "plt.test"
    assert payload["message"] == "fetched ECLI:NL:HR:2024:1"
    assert payload["connector"] == "rechtspraak"
    assert payload["timestamp"].endswith("+00:00")


def test_configure_logging_is_idempotent() -> None:
    settings = build_settings(log_format=LogFormat.TEXT)

    configure_logging(settings, force=True)
    configure_logging(settings)

    assert len(logging.getLogger().handlers) == 1


def test_configure_logging_applies_the_level() -> None:
    settings = build_settings(log_level="warning")

    configure_logging(settings, force=True)

    assert logging.getLogger().level == logging.WARNING


def test_get_logger_returns_a_module_logger() -> None:
    assert get_logger("plt.example").name == "plt.example"
