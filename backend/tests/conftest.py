"""Shared pytest fixtures.

Fixtures build settings explicitly with ``_env_file=None`` so a developer's local ``.env``
can never change a test result.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
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
from plt.db.models import (
    Case,
    CaseDocument,
    CaseTopic,
    Citation,
    Court,
    DocumentType,
    Jurisdiction,
    JurisdictionType,
    KeywordMatch,
    LawDomain,
    Party,
    PartyRole,
    Topic,
)
from plt.db.session import create_session_factory, make_engine
from plt.extensions import dispose_database

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
def app(settings: Settings) -> Iterator[Flask]:
    """Return an application built from the isolated test settings.

    The application's engine is disposed afterwards, so a test that made a request leaves
    no connection open on the temporary database file.
    """
    application = create_app(settings)
    try:
        yield application
    finally:
        dispose_database(application)


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
def api_corpus(seeded_session: Session) -> Session:
    r"""Commit a small, hand-checkable corpus to the database the application reads.

    The API fixtures (``client``) and the database fixtures (``db_session``) are built from
    the same :func:`settings` instance and therefore share one temporary SQLite file, so a
    committed corpus here is what an HTTP request sees. It is committed rather than flushed
    for exactly that reason: the application connects on its own connection.

    Every row states a fact a test asserts on:

    * ``ECLI:NL:RVS:2024:1`` - published Dutch case, public law, spray-drift topic, a court,
      a party, a document, a keyword match and a citation.
    * ``ECLI:NL:RBDHA:2023:2`` - published Dutch case, private law, a second court.
    * ``62019CJ0616`` - published EU case in English.
    * ``ECLI:NL:RVS:2024:5`` - the only case whose text contains ``%``, ``_`` and ``\\``.
    * ``ECLI:NL:RVS:2025:9`` - unpublished, and the newest, so any leak is conspicuous.
    """
    council = Court(
        jurisdiction_code="NL",
        source_identifier="https://data.rechtspraak.nl/Instantie/RVS",
        name="Raad van State",
        level="supreme",
        domain="administrative",
    )
    district = Court(
        jurisdiction_code="NL",
        source_identifier="https://data.rechtspraak.nl/Instantie/RBDHA",
        name="Rechtbank Den Haag",
        level="first_instance",
        domain="civil",
    )
    drift = Topic(slug="spray-drift", label="Spray drift")
    seeded_session.add_all([council, district, drift])
    seeded_session.flush()

    licence = Case(
        jurisdiction_code="NL",
        source_id="ECLI:NL:RVS:2024:1",
        source_system="rechtspraak",
        court_id=council.id,
        title="Glyphosate licence review",
        abstract="Appeal against a plant protection product authorisation.",
        decision_date=date(2024, 5, 1),
        publication_date=date(2024, 5, 3),
        case_numbers=["202301234/1/A3"],
        language="nl",
        law_domain=LawDomain.PUBLIC,
        law_subfield="environmental",
        procedure_type="appeal",
        outcome="dismissed",
        source_url="https://example.invalid/rvs-2024-1",
    )
    licence.documents.append(
        CaseDocument(
            language="nl",
            doc_type=DocumentType.JUDGMENT,
            format="xml",
            full_text=(
                "The authorisation of glyphosate near orchards was reviewed; "
                "no residue limit was applied."
            ),
            raw_payload="<open-rechtspraak>secret payload</open-rechtspraak>",
        )
    )
    licence.parties.append(Party(name="Stichting Milieu", role=PartyRole.APPLICANT))
    licence.topics.append(CaseTopic(topic=drift, confidence=0.8))
    licence.keyword_matches.append(
        KeywordMatch(term_id="nl-001", term="glyfosaat", field="title", list_version="2026.1")
    )
    licence.citations.append(
        Citation(target_identifier="32009R1107", target_scheme="celex", citation_type="cites")
    )

    residue = Case(
        jurisdiction_code="NL",
        source_id="ECLI:NL:RBDHA:2023:2",
        source_system="rechtspraak",
        court_id=district.id,
        title="Pesticide residue in drinking water",
        abstract="Liability for contamination of a catchment area.",
        decision_date=date(2023, 3, 15),
        language="nl",
        law_domain=LawDomain.PRIVATE,
        law_subfield="liability",
    )
    commission = Case(
        jurisdiction_code="EU",
        source_id="62019CJ0616",
        source_system="cellar",
        title="Commission v Kingdom of the Netherlands",
        abstract="Failure to comply with the plant protection products regulation.",
        decision_date=date(2022, 1, 10),
        language="en",
        law_domain=LawDomain.PUBLIC,
    )
    metacharacters = Case(
        jurisdiction_code="NL",
        source_id="ECLI:NL:RVS:2024:5",
        source_system="rechtspraak",
        title="Concentration of 100% in pure_extract",
        abstract=r"Sample stored at C:\field\sample was measured.",
        decision_date=date(2024, 2, 2),
        language="nl",
    )
    unpublished = Case(
        jurisdiction_code="NL",
        source_id="ECLI:NL:RVS:2025:9",
        source_system="rechtspraak",
        title="Draft decision awaiting editorial review",
        decision_date=date(2025, 12, 31),
        language="nl",
        is_published=False,
    )

    seeded_session.add_all([licence, residue, commission, metacharacters, unpublished])
    seeded_session.commit()
    return seeded_session


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
