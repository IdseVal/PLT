"""Session lifecycle: commit on success, roll back on anything else, always close.

A weekly ingestion run is expected to be interruptible (architecture rule 2.2), so the
``KeyboardInterrupt`` path is covered here explicitly rather than assumed to behave like an
ordinary exception.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from plt.config import Settings, get_settings
from plt.db.base import Base
from plt.db.models import Jurisdiction, JurisdictionType
from plt.db.session import (
    create_session_factory,
    dispose_engine,
    get_engine,
    make_engine,
    session_scope,
)
from tests.conftest import build_settings


def netherlands() -> Jurisdiction:
    """Return an unsaved NL jurisdiction row."""
    return Jurisdiction(code="NL", name="Netherlands", type=JurisdictionType.STATE)


def count_jurisdictions(engine: Engine) -> int:
    """Return the number of jurisdiction rows currently committed."""
    with engine.connect() as connection:
        return connection.execute(sa.select(sa.func.count()).select_from(Jurisdiction)).scalar_one()


def test_session_scope_commits_on_success(db_engine: Engine) -> None:
    factory = create_session_factory(db_engine)

    with session_scope(factory) as session:
        session.add(netherlands())

    assert count_jurisdictions(db_engine) == 1


def test_session_scope_rolls_back_on_an_exception(db_engine: Engine) -> None:
    factory = create_session_factory(db_engine)

    with pytest.raises(RuntimeError), session_scope(factory) as session:
        session.add(netherlands())
        session.flush()
        message = "connector failed halfway through the batch"
        raise RuntimeError(message)

    assert count_jurisdictions(db_engine) == 0


def test_session_scope_rolls_back_on_an_interrupt(db_engine: Engine) -> None:
    """Ctrl+C must not leave a half-written batch committed."""
    factory = create_session_factory(db_engine)

    with pytest.raises(KeyboardInterrupt), session_scope(factory) as session:
        session.add(netherlands())
        session.flush()
        raise KeyboardInterrupt

    assert count_jurisdictions(db_engine) == 0


def test_the_sqlite_engine_enforces_foreign_keys(settings: Settings) -> None:
    engine = make_engine(settings)
    try:
        with engine.connect() as connection:
            enabled = connection.execute(sa.text("PRAGMA foreign_keys")).scalar_one()
    finally:
        engine.dispose()

    assert enabled == 1


def test_the_engine_url_comes_from_settings(tmp_path: Path) -> None:
    target = tmp_path / "configured.db"
    engine = make_engine(build_settings(database_url=f"sqlite+pysqlite:///{target}"))
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()

    assert target.exists()


def test_the_process_wide_engine_is_cached_until_disposed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLT_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'cached.db'}")
    get_settings.cache_clear()
    dispose_engine()
    try:
        first = get_engine()

        assert get_engine() is first

        dispose_engine()

        assert get_engine() is not first
    finally:
        dispose_engine()
        get_settings.cache_clear()
