"""HTTP API blueprints.

This module owns blueprint registration. Every blueprint is mounted under
:attr:`plt.config.Settings.api_prefix` so the base path stays configuration, not a literal
repeated across modules.

``/api/health`` lives here rather than in its own module because ``docs/architecture.md``
section 1 fixes the file list for this package. It reports liveness plus, once the pipeline
lands, the last successful ingest per jurisdiction (section 5).
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from flask import Blueprint, Flask

from plt import __version__
from plt.api.cases import cases_bp
from plt.api.stats import stats_bp
from plt.config import Settings

__all__ = ["health_bp", "register_blueprints"]

#: Mounted directly on ``<api_prefix>``.
health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health() -> tuple[dict[str, Any], int]:
    """Report service liveness.

    Returns:
        A ``(payload, status)`` pair. ``ingest`` maps a jurisdiction code onto its last
        successful ingest run; it stays empty until the pipeline and the ``ingest_run``
        table exist, so that the endpoint is dependency-free and can be used as a
        container liveness probe.
    """
    payload: dict[str, Any] = {
        "status": "ok",
        "service": "plt-api",
        "version": __version__,
        "ingest": {},
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
    app.register_blueprint(cases_bp, url_prefix=f"{prefix}/cases")
    app.register_blueprint(stats_bp, url_prefix=f"{prefix}/stats")
