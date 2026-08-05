"""``plt seed-vocabularies``: the court table, filled from each source's own vocabulary.

Courts are matched on the source's vocabulary URI rather than on their name, which is what
makes the command safe to re-run: a second run updates the rows the first one created instead
of doubling them, and a court that is renamed keeps its row and its cases.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from plt.cli import main
from plt.db.base import Base
from plt.db.models import Court, Jurisdiction, JurisdictionType
from plt.db.session import create_session_factory, make_engine
from plt.pipeline import registry
from plt.pipeline.connectors.rechtspraak import RechtspraakConnector
from plt.pipeline.http import PoliteClient
from tests.conftest import build_settings
from tests.unit.test_connector_rechtspraak import (
    CONTENT_URL,
    SEARCH_URL,
    VOCABULARY_URL,
    Endpoint,
    fixture,
)


class SeedingConnector(RechtspraakConnector):
    """The NL connector, wired to the recorded vocabulary instead of the network."""

    def __init__(self, settings: object | None = None) -> None:
        """Serve the recorded ``Instanties`` payload."""
        resolved = build_settings(
            rechtspraak_search_url=SEARCH_URL,
            rechtspraak_content_url=CONTENT_URL,
            rechtspraak_vocabulary_url=VOCABULARY_URL,
        )
        endpoint = Endpoint(vocabulary=fixture("instanties.xml"))
        super().__init__(
            resolved,
            client=PoliteClient(
                resolved,
                transport=httpx.MockTransport(endpoint),
                sleep=lambda _: None,
            ),
        )


class UnreachableVocabulary(SeedingConnector):
    """The NL connector whose vocabulary endpoint is down."""

    def __init__(self, settings: object | None = None) -> None:
        """Answer every vocabulary request with a 503."""
        resolved = build_settings(
            rechtspraak_vocabulary_url=VOCABULARY_URL,
            http_max_retries=0,
        )
        super(RechtspraakConnector, self).__init__(resolved)
        self._client = PoliteClient(
            resolved,
            transport=httpx.MockTransport(Endpoint(vocabulary=None)),
            sleep=lambda _: None,
        )
        self._owns_client = True
        self._courts = None


@pytest.fixture
def seeding_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[sessionmaker[Session]]:
    """Point the CLI at a temporary database holding the Dutch jurisdiction."""
    settings = build_settings(database_url=f"sqlite+pysqlite:///{tmp_path / 'plt.db'}")
    engine = make_engine(settings)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        session.add(
            Jurisdiction(
                code="NL", name="Netherlands", type=JurisdictionType.STATE, map_feature_id="NL"
            )
        )
        session.commit()

    monkeypatch.setattr("plt.cli.get_settings", lambda: settings)
    monkeypatch.setattr("plt.cli.get_session_factory", lambda: factory)
    registry.reset_registry(SeedingConnector)
    try:
        yield factory
    finally:
        registry.reset_registry()
        engine.dispose()


def count_courts(factory: sessionmaker[Session]) -> int:
    """Count the stored courts.

    Args:
        factory: Session factory for the temporary database.

    Returns:
        The number of ``court`` rows.
    """
    with factory() as session:
        return int(session.scalar(select(func.count()).select_from(Court)) or 0)


def test_seeding_fills_the_court_table_from_the_vocabulary(
    seeding_env: sessionmaker[Session],
) -> None:
    assert main(["seed-vocabularies", "-j", "NL"]) == 0
    assert count_courts(seeding_env) == 15

    with seeding_env() as session:
        council = session.scalars(select(Court).where(Court.abbreviation == "RVS")).one()
        assert council.name == "Raad van State"
        assert council.level == "supreme"
        assert council.domain == "administrative"
        assert council.source_type == "TypeRvS"
        # Identity is the vocabulary URI, never the name.
        assert council.source_identifier.startswith("http")


def test_seeding_stores_the_raw_court_type_beside_the_normalised_one(
    seeding_env: sessionmaker[Session],
) -> None:
    """Issue #72: the type reaches the row, and re-seeding is what backfills it.

    The Caribbean courts of the Kingdom normalise onto level ``other`` together with the
    residual instances, so ``source_type`` is the only column that distinguishes them. The
    source states it while the vocabulary is read and nowhere else, which is why this command
    — one request per source, upserting on the vocabulary URI — is the whole backfill, and no
    case has to be fetched again.
    """
    assert main(["seed-vocabularies", "-j", "NL"]) == 0

    with seeding_env() as session:
        kingdom = session.scalars(
            select(Court).where(Court.source_type == "Koninkrijksinstantie").order_by(Court.name)
        ).all()

    assert [court.abbreviation for court in kingdom] == ["OCHM", "OGEAM"]
    assert {court.level for court in kingdom} == {"other"}
    assert kingdom[1].source_metadata == {
        "type": "Koninkrijksinstantie",
        "begin_date": "1950-01-01",
        "end_date": None,
    }


def test_seeding_twice_changes_nothing(seeding_env: sessionmaker[Session]) -> None:
    assert main(["seed-vocabularies", "-j", "NL"]) == 0
    first = count_courts(seeding_env)

    assert main(["seed-vocabularies", "--all"]) == 0

    assert count_courts(seeding_env) == first


def test_a_vocabulary_that_cannot_be_read_is_a_failure_not_an_empty_table(
    seeding_env: sessionmaker[Session],
) -> None:
    registry.reset_registry(UnreachableVocabulary)

    assert main(["seed-vocabularies", "-j", "NL"]) == 1
    assert count_courts(seeding_env) == 0


def test_naming_no_jurisdiction_is_a_usage_error(seeding_env: sessionmaker[Session]) -> None:
    del seeding_env

    assert main(["seed-vocabularies"]) == 2
