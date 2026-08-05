"""The Netherlands connector against the live ``data.rechtspraak.nl`` endpoints.

Opt-in: ``pytest -m integration``. Ordinary runs never reach these, so a court's endpoint is
not called by every developer on every commit and CI stays green when it is down for
maintenance.

What these cover that the recorded fixtures cannot is the *contract* — whether the endpoint
still behaves the way the connector assumes. Two of those assumptions were surprises when
they were first measured and would fail silently rather than loudly if they changed:
``modified`` is read in Dutch local time while the feed's ``updated`` is UTC, and ``return``
accepts ``DOC`` and nothing else. A fixture cannot notice either changing.

The whole module fetches a handful of documents at the configured request rate.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from plt.config import Settings
from plt.db.models import DocumentType
from plt.pipeline.base import Candidate
from plt.pipeline.connectors.rechtspraak import RechtspraakConnector
from tests.conftest import build_settings

pytestmark = pytest.mark.integration

#: A window in the recent past, small enough to be one page and old enough to be settled.
WINDOW_END = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
WINDOW_START = WINDOW_END - timedelta(hours=2)


@pytest.fixture
def live() -> Iterator[RechtspraakConnector]:
    """Return a connector pointed at the real endpoints, closed afterwards."""
    settings: Settings = build_settings(pipeline_page_size=5)
    connector = RechtspraakConnector(settings)
    try:
        yield connector
    finally:
        connector.close()


def test_the_feed_still_answers_the_window_the_connector_composes(
    live: RechtspraakConnector,
) -> None:
    found = []
    for candidate in live.discover(WINDOW_START, WINDOW_END):
        found.append(candidate)
        if len(found) >= 5:
            break

    assert found
    for candidate in found:
        assert candidate.source_id.startswith("ECLI:NL:")
        assert candidate.modified_at is not None
        # The local-time conversion is the whole point: an off-by-two-hours window would
        # still return documents, just the wrong ones.
        assert WINDOW_START <= candidate.modified_at <= WINDOW_END
        assert candidate.content_hash is not None


def test_a_live_document_normalises_into_the_schema(live: RechtspraakConnector) -> None:
    candidate = next(iter(live.discover(WINDOW_START, WINDOW_END)))

    case = live.normalise(live.fetch(candidate))

    assert case.source_id == candidate.source_id
    assert case.jurisdiction_code == "NL"
    assert case.language == "nl"
    assert case.decision_date is not None
    assert case.court is not None
    assert case.court.source_identifier.startswith("http")
    assert case.documents
    assert case.documents[0].raw_payload
    assert case.documents[0].doc_type in {
        DocumentType.JUDGMENT,
        DocumentType.OPINION,
        DocumentType.OTHER,
    }
    assert "dublin_core" in case.source_metadata


def test_the_court_vocabulary_is_still_published(live: RechtspraakConnector) -> None:
    courts = list(live.iter_courts())

    assert len(courts) > 100
    assert all(court.source_identifier for court in courts)
    assert any(court.level == "supreme" for court in courts)
    # The raw type is the only thing that separates the Caribbean courts of the Kingdom from
    # every other instance the normalised level flattens onto ``other`` (issue #72). Sixteen
    # of them on 5 August 2026; the count is not asserted, only that the type is still stated.
    kingdom = [court for court in courts if court.source_type == "Koninkrijksinstantie"]
    assert kingdom
    assert all(court.level == "other" for court in kingdom)


def test_a_kingdom_judgment_still_resolves_against_the_vocabulary(
    live: RechtspraakConnector,
) -> None:
    """The one assumption a fixture cannot notice changing, on the record it was found in.

    The portal qualifies the attribute identifying the court as ``psi:resourceIdentifier`` for
    the Caribbean courts and leaves it bare for the European Dutch ones. If that ever
    converges, this test fails and the connector can stop reading the attribute by its local
    name; if it changes the other way, this is what catches the Kingdom judgments silently
    losing their court again (issue #72).
    """
    candidate = Candidate(source_id="ECLI:NL:OGEAM:2025:155", jurisdiction_code="NL")

    case = live.normalise(live.fetch(candidate))

    assert case.court is not None
    assert case.court.source_identifier == "http://psi.rechtspraak.nl/GEASM"
    assert case.court.source_type == "Koninkrijksinstantie"
    assert case.court.level == "other"
