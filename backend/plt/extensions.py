"""Flask extension objects and their initialisation.

Extensions are created at import time without an application and bound inside
:func:`plt.app.create_app`, so that tests can build several applications with different
settings in one process.

Covers the three cross-cutting concerns named in ``docs/architecture.md`` section 1:
database session lifecycle, CORS and rate limiting. CORS is restricted to the configured
origins and the rate limiter's limits and storage backend come from settings - neither is
ever a literal (section 5, security requirements).

The database binding is per application rather than per process: the engine is built from
the settings the application was created with, lazily on first use, so an application that
never touches the database never opens one. A view obtains its session with
:func:`db_session`; the session is bound to the application context and closed by a
teardown handler whether the view succeeded or raised. Endpoints that stream their
response take their own session from :func:`session_factory` instead, because their work
outlives the request context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Flask, current_app, g
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy.orm import Session, sessionmaker

from plt.config import Settings
from plt.db.session import create_session_factory, make_engine
from plt.utils.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

__all__ = [
    "cors",
    "current_settings",
    "db_session",
    "dispose_database",
    "init_extensions",
    "limiter",
    "session_factory",
]

log = get_logger(__name__)

#: Key the per-application database binding is stored under in ``app.extensions``.
_DATABASE_KEY = "plt_database"

#: Attribute the request-scoped session is stored under on :data:`flask.g`.
_SESSION_ATTR = "plt_session"

#: Application config key holding the validated settings, set by the application factory.
_SETTINGS_KEY = "PLT_SETTINGS"

#: CORS handler; the allowed origins are supplied per application in :func:`init_extensions`.
cors = CORS()


def _default_rate_limit() -> str:
    """Return the configured default rate limit for the current application.

    Supplied to the limiter as a callable rather than as a config value on purpose:
    Flask-Limiter parses ``RATELIMIT_DEFAULT`` only for the first application an extension
    instance is bound to, so a second application built in the same process - which is what
    every test does - would silently inherit the first one's limit. Resolving the limit per
    request keeps it configuration in fact and not only on paper.

    Returns:
        A limit expression such as ``120 per minute``.
    """
    return current_settings().rate_limit_default


#: Rate limiter keyed on the remote address. The default limit is applied per endpoint;
#: ``/api/cases/export`` carries a stricter one of its own.
limiter = Limiter(key_func=get_remote_address, default_limits=[_default_rate_limit])


class _Database:
    """One application's engine and session factory, created on first use.

    Attributes:
        settings: The settings the engine is built from.
    """

    def __init__(self, settings: Settings) -> None:
        """Store the settings without touching the database.

        Args:
            settings: Validated settings supplying the connection parameters.
        """
        self.settings = settings
        self._engine: Engine | None = None
        self._factory: sessionmaker[Session] | None = None

    @property
    def engine(self) -> Engine:
        """Return the application's engine, creating it on first access."""
        if self._engine is None:
            self._engine = make_engine(self.settings)
        return self._engine

    @property
    def factory(self) -> sessionmaker[Session]:
        """Return the application's session factory, creating it on first access."""
        if self._factory is None:
            self._factory = create_session_factory(self.engine)
        return self._factory

    def dispose(self) -> None:
        """Dispose of the engine and drop the cached factory.

        Safe to call when no engine was ever created, and safe to call twice.
        """
        if self._engine is not None:
            self._engine.dispose()
        self._engine = None
        self._factory = None


def current_settings() -> Settings:
    """Return the settings the current application was created with.

    Returns:
        The validated :class:`~plt.config.Settings` instance.

    Raises:
        RuntimeError: If the application was not built by :func:`plt.app.create_app`, and
            therefore carries no settings.
    """
    settings = current_app.config.get(_SETTINGS_KEY)
    if not isinstance(settings, Settings):
        message = "application config holds no validated Settings instance"
        raise RuntimeError(message)
    return settings


def session_factory() -> sessionmaker[Session]:
    """Return the current application's session factory.

    Streaming endpoints call this to open a session whose lifetime they manage themselves,
    because the response body is produced after the view has returned.

    Returns:
        The application's :class:`sqlalchemy.orm.sessionmaker`.

    Raises:
        RuntimeError: If the application has no database binding.
    """
    database = current_app.extensions.get(_DATABASE_KEY)
    if not isinstance(database, _Database):
        message = "application has no database binding; was init_extensions() called?"
        raise RuntimeError(message)
    return database.factory


def db_session() -> Session:
    """Return the session bound to the current application context.

    The first call in a request opens a session; every later call in the same request
    returns it. :func:`_close_session` closes it when the context is torn down, so a view
    never has to, and an exception can never leave a connection checked out.

    Returns:
        An open :class:`sqlalchemy.orm.Session`.
    """
    session: Session | None = g.get(_SESSION_ATTR)
    if session is None:
        session = session_factory()()
        setattr(g, _SESSION_ATTR, session)
    return session


def _close_session(exception: BaseException | None) -> None:
    """Roll back and close the request-scoped session, if one was opened.

    Args:
        exception: The exception that ended the application context, if any.
    """
    session: Session | None = g.pop(_SESSION_ATTR, None)
    if session is None:
        return
    try:
        if exception is not None:
            session.rollback()
    finally:
        session.close()


def dispose_database(app: Flask) -> None:
    """Dispose of an application's engine.

    Used by tests and by a shutting-down process so no pooled connection outlives the
    application that opened it.

    Args:
        app: The application whose engine should be disposed of.
    """
    database = app.extensions.get(_DATABASE_KEY)
    if isinstance(database, _Database):
        database.dispose()


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
    # limits themselves in settings and out of this module. The default limit is the one
    # exception: see :func:`_default_rate_limit`.
    app.config["RATELIMIT_ENABLED"] = settings.rate_limit_enabled
    app.config["RATELIMIT_STORAGE_URI"] = settings.rate_limit_storage_uri
    app.config["RATELIMIT_HEADERS_ENABLED"] = True
    limiter.init_app(app)

    app.extensions[_DATABASE_KEY] = _Database(settings)
    app.teardown_appcontext(_close_session)

    log.debug(
        "extensions initialised",
        extra={
            "cors_origins": settings.cors_allowed_origins,
            "rate_limit_enabled": settings.rate_limit_enabled,
        },
    )
