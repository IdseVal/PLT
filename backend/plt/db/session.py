"""Engine and session lifecycle.

Connection parameters come from :class:`plt.config.Settings` (``database_url``,
``database_echo``, ``database_pool_size``, ``database_pool_timeout_seconds``); nothing in
this module hard-codes a URL.

The engine is created once per process and cached. :func:`session_scope` is the only way the
pipeline and the API should obtain a session: it commits on success, rolls back on *any*
exception including :class:`KeyboardInterrupt`, and always releases the connection in a
``finally`` block — which is what lets a long ingestion run be interrupted without leaving a
half-written batch behind (architecture rules 2.2 and 2.3).

SQLite needs ``PRAGMA foreign_keys=ON`` per connection; without it the ``ON DELETE CASCADE``
clauses in the schema are parsed and ignored, and development would behave differently from
a PostgreSQL deployment. The pragma is installed by a connect listener here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from sqlalchemy import event
from sqlalchemy.engine import Engine, create_engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from plt.config import Settings, get_settings
from plt.utils.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.pool import ConnectionPoolEntry

__all__ = [
    "create_session_factory",
    "dispose_engine",
    "get_engine",
    "get_session_factory",
    "make_engine",
    "session_scope",
]

log = get_logger(__name__)


def _install_sqlite_pragmas(engine: Engine) -> None:
    """Enable SQLite foreign-key enforcement for every connection of an engine.

    Args:
        engine: The engine whose connections should enforce foreign keys.
    """

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection: Any, connection_record: ConnectionPoolEntry) -> None:  # noqa: ANN401 - the DBAPI connection is untyped by design
        del connection_record
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def make_engine(settings: Settings | None = None) -> Engine:
    """Build a new engine from settings.

    Pool options are only meaningful for a client/server database; SQLite's default
    ``QueuePool`` substitute rejects them, so they are applied for other drivers only.

    Args:
        settings: Settings to read the connection parameters from. Defaults to the
            process-wide settings.

    Returns:
        A fresh :class:`sqlalchemy.engine.Engine`. The caller owns its disposal.
    """
    settings = settings or get_settings()
    url = make_url(settings.database_url)
    options: dict[str, Any] = {
        "echo": settings.database_echo,
        # A weekly run can sit idle between batches long enough for a server to drop the
        # connection; pre-ping replaces a dead one instead of failing the run.
        "pool_pre_ping": True,
    }
    if url.get_backend_name() == "sqlite":
        # A Flask worker and the pipeline both hand connections between threads.
        options["connect_args"] = {"check_same_thread": False}
    else:
        options["pool_size"] = settings.database_pool_size
        options["pool_timeout"] = settings.database_pool_timeout_seconds

    engine = create_engine(url, **options)
    if url.get_backend_name() == "sqlite":
        _install_sqlite_pragmas(engine)

    log.debug(
        "database engine created",
        # render_as_string() masks the password; the raw URL is never logged.
        extra={"url": url.render_as_string(hide_password=True)},
    )
    return engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the process-wide engine, creating it on first use.

    Returns:
        The cached :class:`sqlalchemy.engine.Engine`.
    """
    return make_engine()


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory bound to an engine.

    ``expire_on_commit`` is disabled so that objects stay usable after the per-batch commit
    the pipeline performs, and after the commit that ends an API request.

    Args:
        engine: Engine the sessions connect through.

    Returns:
        A configured :class:`sqlalchemy.orm.sessionmaker`.
    """
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory, creating it on first use.

    Returns:
        The cached :class:`sqlalchemy.orm.sessionmaker`.
    """
    return create_session_factory(get_engine())


@contextmanager
def session_scope(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """Yield a session that commits on success and always closes.

    ``BaseException`` is caught rather than ``Exception`` so that a ``KeyboardInterrupt``
    during a batch rolls the batch back instead of leaving the transaction open while the
    process shuts down.

    Args:
        factory: Session factory to use. Defaults to the process-wide factory.

    Yields:
        An open :class:`sqlalchemy.orm.Session`.

    Raises:
        BaseException: Whatever the body raised, after the transaction is rolled back.
    """
    session = (factory or get_session_factory())()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_engine() -> None:
    """Dispose of the process-wide engine and drop the cached instances.

    Called at interpreter shutdown, between tests, and by the CLI once a run has finished,
    so that no pooled connection outlives the process that opened it.
    """
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_session_factory.cache_clear()
    get_engine.cache_clear()
