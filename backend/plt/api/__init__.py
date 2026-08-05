"""HTTP API blueprints.

This module owns blueprint registration. Every blueprint is mounted under
:attr:`plt.config.Settings.api_prefix` so the base path stays configuration, not a literal
repeated across modules.

``/api/health`` lives here rather than in its own module because ``docs/architecture.md``
section 1 fixes the file list for this package. It reports liveness plus the last
successful ingest per jurisdiction (section 5), which is how an operator sees at a glance
that the weekly job is still landing data. A database that cannot be reached does not make
the endpoint fail: liveness is about the process, so the failure is reported in the
``database`` field and logged, and the probe still answers.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from flask import Blueprint, Flask
from sqlalchemy.exc import SQLAlchemyError

from plt import __version__
from plt.api.cases import cases_bp
from plt.api.reviews import reviews_bp
from plt.api.schemas import health_ingest_payload
from plt.api.stats import filters_bp, stats_bp
from plt.api.subscriptions import subscriptions_bp
from plt.config import Settings
from plt.db.repositories import latest_successful_runs
from plt.extensions import db_session
from plt.utils.logging import get_logger

__all__ = ["health_bp", "register_blueprints"]

log = get_logger(__name__)

#: Mounted directly on ``<api_prefix>``.
health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health() -> tuple[dict[str, Any], int]:
    """Report service liveness and the last successful ingest per jurisdiction.

    Returns:
        A ``(payload, status)`` pair. ``ingest`` maps a jurisdiction code onto the ISO
        timestamp of its last successful ingestion run and stays empty until the pipeline
        has completed one; ``database`` is ``ok`` or ``unavailable``.
    """
    ingest: dict[str, str | None] = {}
    database = "ok"
    try:
        ingest = health_ingest_payload(latest_successful_runs(db_session()))
    except SQLAlchemyError:
        # Never surface the driver's message: it carries the DSN and the failing SQL.
        log.exception("health check could not read the database")
        database = "unavailable"

    payload: dict[str, Any] = {
        "status": "ok",
        "service": "plt-api",
        "version": __version__,
        "database": database,
        "ingest": ingest,
    }
    return payload, HTTPStatus.OK


def register_blueprints(app: Flask, settings: Settings) -> None:
    """Mount every API blueprint on the application.

    Args:
        app: The Flask application to register on.
        settings: Validated settings supplying the API base path.
    """
    prefix = settings.api_prefix
    app.register_blueprint(health_bp, url_prefix=prefix)
    app.register_blueprint(filters_bp, url_prefix=prefix)
    app.register_blueprint(cases_bp, url_prefix=f"{prefix}/cases")
    app.register_blueprint(reviews_bp, url_prefix=f"{prefix}/reviews")
    app.register_blueprint(stats_bp, url_prefix=f"{prefix}/stats")
    app.register_blueprint(subscriptions_bp, url_prefix=f"{prefix}/subscriptions")
