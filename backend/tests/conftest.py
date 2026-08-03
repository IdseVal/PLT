"""Shared pytest fixtures.

Fixtures build settings explicitly with ``_env_file=None`` so a developer's local ``.env``
can never change a test result.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from plt.app import create_app
from plt.config import AppEnv, Settings
from plt.db.base import Base
from plt.db.models import Jurisdiction, JurisdictionType
from plt.db.session import create_session_factory, make_engine

#: Repository root, resolved from ``<repo>/backend/tests/conftest.py``.
REPO_ROOT = Path(__file__).resolve().parents[2]


def build_settings(**overrides: Any) -> Settings:  # noqa: ANN401 - arbitrary field overrides
    """Build settings isolated from the environment.

    ``_env_file=None`` stops pydantic-settings from reading a developer's local ``.env``,
    so a test result never depends on an untracked file. The arguments are collected into a
    mapping first because pydantic's ``dataclass_transform`` signature does not expose the
    underscore-prefixed loader options to a type checker.

    Args:
        **overrides: Field values to override on top of the testing defaults.

    Returns:
        A validated :class:`~plt.config.Settings` instance.
    """
    params: dict[str, Any] = {"_env_file": None, "app_env": AppEnv.TESTING, **overrides}
    return Settings(**params)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return isolated test settings backed by a temporary SQLite database."""
    return build_settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        rate_limit_enabled=False,
    )


@pytest.fixture
def app(settings: Settings) -> Flask:
    """Return an application built from the isolated test settings."""
    return create_app(settings)


@pytest.fixture
def client(app: Flask) -> Iterator[FlaskClient]:
    """Return a test client, closed after the test."""
    with app.test_client() as test_client:
        yield test_client


@pytest.fixture
def db_engine(settings: Settings) -> Iterator[Engine]:
    """Return an engine on a temporary SQLite file with the schema created.

    The schema is built from ``Base.metadata`` rather than by running the migrations, which
    keeps the fixture fast; ``tests/unit/test_migrations.py`` covers the migrations
    themselves and asserts that the two agree.
    """
    engine = make_engine(settings)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    """Return an open session on the temporary database, rolled back and closed after."""
    factory = create_session_factory(db_engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def seeded_session(db_session: Session) -> Session:
    """Return a session with the two launch jurisdictions inserted, as migration 0002 does."""
    db_session.add_all(
        [
            Jurisdiction(
                code="EU",
                name="European Union",
                type=JurisdictionType.SUPRANATIONAL,
                map_feature_id="EU",
                default_language="en",
            ),
            Jurisdiction(
                code="NL",
                name="Netherlands",
                type=JurisdictionType.STATE,
                iso_alpha2="NL",
                map_feature_id="NL",
                default_language="nl",
            ),
        ]
    )
    db_session.flush()
    return db_session
