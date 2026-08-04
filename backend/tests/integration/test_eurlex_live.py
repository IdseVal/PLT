"""The EU connector against the live CELLAR endpoints.

Opt-in: ``pytest -m integration``. Nothing here runs in the default suite, which is served
entirely by the recorded fixtures in ``tests/unit/test_connector_eurlex.py``.

These tests exist for one reason the fixtures cannot serve: CELLAR is a live repository run
by the Publications Office, and the facts this connector is built on — that
``cdm:resource_legal_id_sector`` is an ``xsd:string``, that a manifestation is negotiated
with ``application/xhtml+xml`` rather than ``text/html``, that a parenthesised CELEX number
has to be percent-encoded — were all verified by hand on 4 August 2026 and can change
without notice. Running these is how the project finds out.

They are deliberately small. The window is two days wide, three documents are fetched, and
everything goes through the same politeness throttle as a real run.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest

from plt.config import EurLexDiscoveryDate, Settings
from plt.pipeline.base import Candidate, DocumentUnavailableError
from plt.pipeline.connectors.eurlex import EurLexConnector
from plt.pipeline.filters.keywords import KeywordFilter
from tests.conftest import build_settings

pytestmark = pytest.mark.integration

#: The Blaise judgment: a preliminary reference on Regulation (EC) No 1107/2009 and
#: glyphosate, and about as unambiguously a pesticide case as the corpus holds.
BLAISE = "62017CJ0616"

#: A corrigendum, whose parenthesised suffix must be percent-encoded into the path.
CORRIGENDUM = "62021TO0601(01)"


def live_settings(**overrides: Any) -> Settings:  # noqa: ANN401 - arbitrary field overrides
    """Return settings pointed at the live endpoints, at the shipped request rate.

    Args:
        **overrides: Fields to override.

    Returns:
        Validated settings. The endpoints are the defaults, so a deployment that has
        repointed them is what these tests then check.
    """
    return build_settings(**overrides)


@pytest.fixture
def connector() -> Iterator[EurLexConnector]:
    """Return a connector against the live endpoints, closed after the test."""
    built = EurLexConnector(live_settings())
    try:
        yield built
    finally:
        built.close()


def test_discovery_finds_case_law_in_a_two_day_window(connector: EurLexConnector) -> None:
    found = []
    for candidate in connector.discover(
        datetime(2019, 10, 1, tzinfo=UTC), datetime(2019, 10, 3, tzinfo=UTC)
    ):
        found.append(candidate)
        if len(found) >= 5:
            break

    assert found, "CELLAR returned no case law at all for a two-day modification window"
    assert all(candidate.jurisdiction_code == "EU" for candidate in found)
    assert all(candidate.source_id.startswith("6") for candidate in found)


def test_discovery_by_document_date_finds_the_decisions_of_a_day() -> None:
    built = EurLexConnector(live_settings(eurlex_discovery_date=EurLexDiscoveryDate.DOCUMENT))

    try:
        found = list(
            built.discover(datetime(2019, 10, 1, tzinfo=UTC), datetime(2019, 10, 2, tzinfo=UTC))
        )
    finally:
        built.close()

    assert found
    assert BLAISE in {candidate.source_id for candidate in found}
    dates = {candidate.decision_date for candidate in found if candidate.decision_date}
    assert dates == {date(2019, 10, 1)}


def test_a_known_pesticide_judgment_still_maps_onto_the_schema(
    connector: EurLexConnector,
) -> None:
    """Everything the issue asks to capture, checked against the live notice."""
    case = connector.normalise(connector.fetch(Candidate(source_id=BLAISE, jurisdiction_code="EU")))

    assert case.source_id == BLAISE
    assert case.source_metadata["ecli"] == "ECLI:EU:C:2019:800"
    assert case.source_metadata["cellar_uri"].startswith("http://publications.europa.eu/resource/")
    assert case.case_numbers == ("C-616/17",)
    assert case.decision_date == date(2019, 10, 1)
    assert case.court is not None
    assert case.court.name == "Court of Justice"
    assert case.procedure_type == "Reference for a preliminary ruling"
    assert case.subject and "Plant protection products" in case.subject
    assert case.source_metadata["procedure_language"] == "FRA"
    assert len(case.source_metadata["available_languages"]) >= 20
    assert any(citation.target_identifier == "32009R1107" for citation in case.citations)
    texts = [document for document in case.documents if document.has_text]
    assert [document.language for document in texts] == ["en"]
    assert "glyphosate" in (case.full_text or "").lower()


def test_the_live_judgment_passes_the_curated_eu_list(connector: EurLexConnector) -> None:
    """The evidence that the connector and the keyword list actually meet."""
    case = connector.normalise(connector.fetch(Candidate(source_id=BLAISE, jurisdiction_code="EU")))

    result = KeywordFilter.for_jurisdiction("EU", settings=live_settings()).evaluate(case)

    assert result.passed, result.reason


def test_a_parenthesised_celex_number_is_still_retrievable(connector: EurLexConnector) -> None:
    """CELLAR 404s the unencoded form; a regression here fails documents every week."""
    raw = connector.fetch(Candidate(source_id=CORRIGENDUM, jurisdiction_code="EU"))

    assert raw.payload.lstrip().startswith("<?xml")
    assert connector.normalise(raw).source_id == CORRIGENDUM


def test_an_unknown_celex_number_is_reported_as_a_missing_document(
    connector: EurLexConnector,
) -> None:
    with pytest.raises(DocumentUnavailableError):
        connector.fetch(Candidate(source_id="69999CJ9999", jurisdiction_code="EU"))
