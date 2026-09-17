"""Flask application factory.

Run the development server with::

    flask --app plt.app run

The factory takes optional settings so tests can build an application against an isolated
configuration without touching the environment.
"""

from __future__ import annotations

from flask import Flask

from plt.api import register_blueprints
from plt.api.errors import register_error_handlers
from plt.cli import plt_cli
from plt.config import Settings, get_settings
from plt.extensions import init_extensions
from plt.pipeline.filters.legislation import LegislationListError, load_legislation_list_for
from plt.pipeline.registry import available_jurisdictions
from plt.utils.logging import configure_logging, get_logger

__all__ = ["create_app"]

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        settings: Settings to build the application with. Defaults to the process-wide
            settings read from the environment.

    Returns:
        A configured :class:`flask.Flask` application with extensions bound, blueprints
        mounted and the uniform error envelope registered.
    """
    settings = settings or get_settings()
    configure_logging(settings)

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=settings.secret_key.get_secret_value(),
        DEBUG=settings.debug,
        TESTING=settings.is_testing,
        JSON_SORT_KEYS=False,
        # Session cookies are hardened even though the API is currently stateless, so that
        # adding an authenticated admin surface later cannot regress these defaults.
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=settings.is_production,
        # Keep the settings object reachable from the request context.
        PLT_SETTINGS=settings,
    )

    init_extensions(app, settings)
    register_error_handlers(app)
    register_blueprints(app, settings)
    _warm_legislation_lists(settings)

    # `flask --app plt.app plt ...` and `python -m plt.cli ...` reach the same commands, so
    # a server cron and the scheduled workflow run identical code (architecture section 7).
    app.cli.add_command(plt_cli)

    log.info(
        "application created",
        extra={"environment": str(settings.app_env), "api_prefix": settings.api_prefix},
    )
    return app


def _warm_legislation_lists(settings: Settings) -> None:
    """Compile every registered jurisdiction's legislation list before the first request.

    A list of two thousand instruments compiles in well under a second, and ``/api/filters``
    reads every one of them; paying that once at start-up rather than on whichever request
    happens to arrive first keeps the first reader from waiting on it. A list that cannot
    be loaded is logged and left alone: the endpoint skips it, and an ingest run refuses it
    loudly, which is where a broken list should surface.

    Args:
        settings: Settings resolving the legislation directory.
    """
    for code in available_jurisdictions():
        try:
            load_legislation_list_for(code, settings)
        except LegislationListError as error:
            log.warning(
                "legislation list not loaded at start-up",
                extra={"context": {"jurisdiction": code, "error": str(error)}},
            )
