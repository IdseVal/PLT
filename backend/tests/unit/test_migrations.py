"""The migrations must build the schema the models describe, and be able to undo it.

These tests run Alembic for real against a throwaway SQLite file: upgrade to head,
inspect the result, downgrade to base, and confirm that autogenerate finds nothing left to
do — which is the check that the revisions and ``plt.db.models`` have not drifted apart.

Repeatability is checked twice, because an empty database is the easy half of it. A rollback
on a *populated* database leaves rows behind that a revision has to expect on the way back
up, and that is the half a seed migration gets wrong.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Enum, create_engine, inspect, text
from sqlalchemy.orm import Session

from plt.config import get_settings
from plt.db.base import NAMING_CONVENTION, Base, include_object
from plt.db.models import Case
from tests.conftest import REPO_ROOT

BACKEND = REPO_ROOT / "backend"

#: Every table the contract requires, plus Alembic's own bookkeeping table.
EXPECTED_TABLES = {
    "alembic_version",
    "case",
    "case_document",
    "case_review",
    "case_review_decision",
    "case_topic",
    "citation",
    "court",
    "ingest_checkpoint",
    "ingest_run",
    "jurisdiction",
    "keyword_match",
    "party",
    "subscriber",
    "topic",
}


@pytest.fixture
def alembic_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    """Return an Alembic config pointed at an empty, temporary SQLite database.

    ``migrations/env.py`` reads the URL from :class:`plt.config.Settings`, so the
    environment variable is what redirects it; the settings cache is cleared on both sides
    so no other test sees the override.
    """
    url = f"sqlite+pysqlite:///{tmp_path / 'migrated.db'}"
    monkeypatch.setenv("PLT_DATABASE_URL", url)
    get_settings.cache_clear()

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    try:
        yield config
    finally:
        get_settings.cache_clear()


def database_url(config: Config) -> str:
    url = config.get_main_option("sqlalchemy.url")
    assert url is not None
    return url


def test_upgrade_creates_every_table_on_an_empty_database(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")

    engine = create_engine(database_url(alembic_config))
    try:
        assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES
    finally:
        engine.dispose()


def test_upgrade_enforces_the_dedup_key_and_the_required_indexes(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")

    engine = create_engine(database_url(alembic_config))
    try:
        inspector = inspect(engine)
        unique = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("case")
        }
        indexed = {
            table: {tuple(index["column_names"]) for index in inspector.get_indexes(table)}
            for table in ("case", "case_document", "keyword_match")
        }
    finally:
        engine.dispose()

    assert ("jurisdiction_code", "source_id") in unique
    assert ("decision_date",) in indexed["case"]
    assert ("jurisdiction_code",) in indexed["case"]
    assert ("content_hash",) in indexed["case"]
    assert ("case_id",) in indexed["case_document"]
    assert ("case_id",) in indexed["keyword_match"]


def test_the_seed_migration_inserts_the_launch_jurisdictions(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")

    engine = create_engine(database_url(alembic_config))
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT code, name, type, map_feature_id FROM jurisdiction ORDER BY code")
            ).all()
    finally:
        engine.dispose()

    assert [tuple(row) for row in rows] == [
        ("EU", "European Union", "supranational", "EU"),
        ("NL", "Netherlands", "state", "NL"),
    ]


def test_migrations_and_models_do_not_drift(alembic_config: Config) -> None:
    """Autogenerate against the migrated database must find nothing to change.

    ``include_object`` is the same filter ``migrations/env.py`` installs, so this asserts
    against exactly what ``alembic revision --autogenerate`` would produce. It excludes the
    CHECK constraints ``portable_enum`` generates, which alembic cannot compare; their values
    are asserted directly by the test below instead.
    """
    command.upgrade(alembic_config, "head")

    engine = create_engine(database_url(alembic_config))
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_type": True, "include_object": include_object}
            )
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert diff == []


def test_the_migrated_check_constraints_match_the_model_enums(alembic_config: Config) -> None:
    """The enum CHECK constraints in the database must permit exactly the model's values.

    Autogenerate cannot compare these (see :func:`plt.db.base.include_object`), so excluding
    them there would leave an enum member added to a model but not to a migration undetected.
    This closes that gap by reading the constraints out of the migrated database and comparing
    the permitted values against the enums themselves.
    """
    command.upgrade(alembic_config, "head")

    expected = {
        NAMING_CONVENTION["ck"] % {"table_name": table.name, "constraint_name": column.type.name}: (
            set(column.type.enums)
        )
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, Enum) and not column.type.native_enum
    }
    assert expected, "no portable_enum columns found; the models did not import"

    engine = create_engine(database_url(alembic_config))
    try:
        inspector = inspect(engine)
        actual = {
            constraint["name"]: set(re.findall(r"'([^']*)'", str(constraint["sqltext"])))
            for table_name in inspector.get_table_names()
            for constraint in inspector.get_check_constraints(table_name)
            if constraint["name"] in expected
        }
    finally:
        engine.dispose()

    assert actual == expected


def test_downgrade_removes_everything_it_created(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")

    engine = create_engine(database_url(alembic_config))
    try:
        remaining = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert remaining == {"alembic_version"}


def test_upgrade_downgrade_upgrade_is_repeatable(alembic_config: Config) -> None:
    """A re-run after a rollback must land on the same schema, seed rows included."""
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    engine = create_engine(database_url(alembic_config))
    try:
        with engine.connect() as connection:
            count = connection.execute(text("SELECT COUNT(*) FROM jurisdiction")).scalar_one()
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert tables == EXPECTED_TABLES
    assert count == 2


def test_the_seed_is_repeatable_on_a_populated_database(alembic_config: Config) -> None:
    """The case the test above cannot reach: a downgrade that had to retain a seed row.

    ``0002.downgrade`` deliberately keeps a jurisdiction that has cases attached, so that
    unwinding reference data never cascades away ingested data. Going forward again then
    meets a row that is already there, which the test above never sees because it downgrades
    an empty database to base and both rows go. Re-applying the seed has to insert only what
    is missing.
    """
    command.upgrade(alembic_config, "head")
    url = database_url(alembic_config)

    engine = create_engine(url)
    try:
        with Session(engine) as session:
            session.add(
                Case(jurisdiction_code="NL", source_id="ECLI:NL:HR:2026:1", source_system="test")
            )
            session.commit()
    finally:
        engine.dispose()

    command.downgrade(alembic_config, "0001")

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            retained = connection.execute(text("SELECT code FROM jurisdiction")).scalars().all()
    finally:
        engine.dispose()
    # The premise of the test: NL survived the downgrade because a case hangs off it.
    assert list(retained) == ["NL"]

    command.upgrade(alembic_config, "head")

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            codes = (
                connection.execute(text("SELECT code FROM jurisdiction ORDER BY code"))
                .scalars()
                .all()
            )
            cases = connection.execute(text('SELECT COUNT(*) FROM "case"')).scalar_one()
    finally:
        engine.dispose()

    assert list(codes) == ["EU", "NL"]
    assert cases == 1
