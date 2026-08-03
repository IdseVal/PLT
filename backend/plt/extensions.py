"""Flask extension objects and their initialisation.

Extensions are created at import time without an application and bound inside
:func:`plt.app.create_app`, so that tests can build several applications with different
settings in one process.

Covers the three cross-cutting concerns named in ``docs/architecture.md`` section 1:
database session lifecycle, CORS and rate limiting. CORS is restricted to the configured
origins and the rate limiter's limits and storage backend come from settings - neither is
ever a literal (section 5, security requirements).
"""

from __future__ import annotations

from flask import Flask
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from plt.config import Settings
from plt.utils.logging import get_logger

__all__ = ["cors", "init_extensions", "limiter"]

log = get_logger(__name__)

#: CORS handler; the allowed origins are supplied per application in :func:`init_extensions`.
cors = CORS()

#: Rate limiter keyed on the remote address. Default limits come from settings.
limiter = Limiter(key_func=get_remote_address)


def init_extensions(app: Flask, settings: Settings) -> None:
    """Bind the extension objects to an application.

    Args:
        app: The Flask application being created.
        settings: Validated settings supplying origins, limits and storage backend.
    """
    cors.init_app(
        app,
        resources={f"{settings.api_prefix}/*": {"origins": settings.cors_allowed_origins}},
        supports_credentials=False,
    )

    # Flask-Limiter reads its configuration from the application config, which keeps the
    # limits themselves in settings and out of this module.
    app.config["RATELIMIT_ENABLED"] = settings.rate_limit_enabled
    app.config["RATELIMIT_DEFAULT"] = settings.rate_limit_default
    app.config["RATELIMIT_STORAGE_URI"] = settings.rate_limit_storage_uri
    app.config["RATELIMIT_HEADERS_ENABLED"] = True
    limiter.init_app(app)

    log.debug(
        "extensions initialised",
        extra={
            "cors_origins": settings.cors_allowed_origins,
            "rate_limit_enabled": settings.rate_limit_enabled,
        },
    )
