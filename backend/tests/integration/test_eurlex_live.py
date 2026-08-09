"""The EU connector against the live CELLAR endpoints.

Opt-in: ``pytest -m integration``. Nothing here runs in the default suite, which is served
entirely by the recorded fixtures in ``tests/unit/test_connector_eurlex.py``.

These tests exist for one reason the fixtures cannot serve: CELLAR is a live repository run
by the Publications Office, and the facts this connector is built on — that
``cdm:resource_legal_id_sector`` is an ``xsd:string``, that a manifestation is negotiated
with ``application/xhtml+xml`` rather than ``text/html``, that a parenthesised CELEX number
has to be percent-encoded — were all verified by hand on 4 August 2026 and can change
without notice. Running these is how the project finds out.

One of them is here for a stronger reason. The recorded fixtures cannot express how the
endpoint *orders* a result set, so the fake had to choose, and it chose to sort stably; that
is why a discovery walk losing a sixth of the corpus passed the whole suite. Ordering is
behaviour, not payload, and behaviour can only be pinned against the live service —
:func:`test_a_paged_window_yields_every_case_the_endpoint_counts` is that pin.

They are deliberately small. The heaviest of them walks a one-day window of a few hundred
cases, three documents are fetched, and everything goes through the same politeness throttle
as a real run.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any, Final

import pytest

from plt.config import EurLexDiscoveryDate, Settings
from plt.pipeline.base import Candidate, DocumentUnavailableError
from plt.pipeline.connectors.eurlex import EurLexConnector
from plt.pipeline.filters.keywords import KeywordFilter
from plt.pipeline.windows import Window
from tests.conftest import build_settings

pytestmark = pytest.mark.integration

#: The Blaise judgment: a preliminary reference on Regulation (EC) No 1107/2009 and
#: glyphosate, and about as unambiguously a pesticide case as the corpus holds.
BLAISE = "62017CJ0616"

#: A corrigendum, whose parenthesised suffix must be percent-encoded into the path.
CORRIGENDUM = "62021TO0601(01)"

#: Page size the paging-integrity walk uses. Small enough to make both windows below five or
#: six pages, which is the only way a paging defect can show at all, and large enough that the
#: whole test costs CELLAR about fifteen queries rather than a flood.
PAGING_PAGE_SIZE: Final = 25

#: Windows the paging-integrity walk covers: 134 and 117 cases as counted on 8 August 2026,
#: both of them cheap.
#:
#: Two, not one, because the endpoint's misbehaviour is not deterministic and a single window
#: can therefore pass while the walk is broken. Measured on this date with the keyset paging
#: reverted: 2023-04-01 to 2023-04-02 lost 6 cases on one run and 8 on the next;
#: 2017-06-01 to 2017-07-01 lost 15 on the first and none at all on the second; and
#: 2023-04-01 to 2023-04-03, which is why it is not used here, lost none on either. Each
#: window is a coin the endpoint tosses, so the guard holds two of them.
PAGING_WINDOWS: Final = [
    pytest.param(datetime(2023, 4, 1, tzinfo=UTC), datetime(2023, 4, 2, tzinfo=UTC), id="2023-04"),
    pytest.param(datetime(2017, 6, 1, tzinfo=UTC), datetime(2017, 7, 1, tzinfo=UTC), id="2017-06"),
]


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


@pytest.mark.parametrize(("start", "stop"), PAGING_WINDOWS)
def test_a_paged_window_yields_every_case_the_endpoint_counts(
    start: datetime, stop: datetime
) -> None:
    """The union of the pages of a window must be the window.

    This is the test that would have caught the offset-paging defect, and that no fixture
    could have: the walk is checked against an oracle the paging cannot influence — CELLAR's
    own ``COUNT(DISTINCT ?celex)`` over the identical graph pattern — rather than against
    itself. Every guard the pipeline already had was of the second kind, which is why a
    discovery walk missing a sixth of the corpus reported ``success`` with zero failures:
    ``discovered == mirrored + skipped`` held exactly, over the wrong set.

    Both directions are asserted, because the defect produced both. Some CELEX came back on
    two pages and others on none, so the walk returned the right *number* of rows while
    holding the wrong *set*; counting rows would have missed it entirely.

    Args:
        start: Inclusive lower bound of the window.
        stop: Exclusive upper bound.
    """
    connector = EurLexConnector(live_settings(pipeline_page_size=PAGING_PAGE_SIZE))
    try:
        expected = connector._count(Window(start, stop))
        found = [candidate.source_id for candidate in connector.discover(start, stop)]
    finally:
        connector.close()

    assert expected > PAGING_PAGE_SIZE, (
        f"the window {start:%Y-%m-%d} to {stop:%Y-%m-%d} now counts {expected} cases, which is "
        f"one page at a size of {PAGING_PAGE_SIZE}; it no longer tests paging and needs "
        f"re-picking"
    )
    distinct = set(found)
    duplicated = sorted(celex for celex, seen in Counter(found).items() if seen > 1)
    lost = expected - len(distinct)
    # One assertion for both directions, so a failure reports the whole shape of the loss
    # rather than whichever half of it pytest reached first.
    assert not lost and not duplicated, (
        f"the paged walk of {start:%Y-%m-%d} to {stop:%Y-%m-%d} at page size "
        f"{PAGING_PAGE_SIZE} returned {len(found)} rows holding {len(distinct)} distinct "
        f"CELEX, against {expected} the endpoint counts in the same window: {lost} lost "
        f"({lost / expected:.1%}), {len(duplicated)} returned more than once {duplicated[:5]}"
    )


@pytest.mark.parametrize(("start", "stop"), PAGING_WINDOWS)
def test_a_paged_listing_yields_every_identifier_the_endpoint_counts(
    start: datetime, stop: datetime
) -> None:
    """The union of the pages of the identifier listing must be the listing.

    The same oracle as the discovery walk above, applied to the query a repair depends on,
    and for the same reason: the fake in ``test_connector_eurlex.py`` had to invent how the
    endpoint pages a ``DISTINCT``, and behaviour a fake invents is behaviour nothing has
    checked (``docs/architecture.md`` rule 2.9). A listing that silently dropped identifiers
    would tell a repair that the cases it never listed are already held — the absence nobody
    can audit, which is exactly what this mode exists to fix.

    It is bounded to a window rather than run over the whole of sector 6 so that the test
    stays as cheap as its neighbours: rule 2.10 applies to the test suite too.

    Args:
        start: Inclusive lower bound of the window listed.
        stop: Exclusive upper bound.
    """
    connector = EurLexConnector(live_settings(eurlex_identifier_page_size=PAGING_PAGE_SIZE))
    try:
        expected = connector._count(Window(start, stop))
        listed = [candidate.source_id for candidate in connector.enumerate_identifiers(start, stop)]
    finally:
        connector.close()

    assert expected > PAGING_PAGE_SIZE, (
        f"the window {start:%Y-%m-%d} to {stop:%Y-%m-%d} now counts {expected} cases, which is "
        f"one page at a size of {PAGING_PAGE_SIZE}; it no longer tests paging"
    )
    distinct = set(listed)
    duplicated = sorted(celex for celex, seen in Counter(listed).items() if seen > 1)
    assert len(distinct) == expected and not duplicated, (
        f"the listing of {start:%Y-%m-%d} to {stop:%Y-%m-%d} at page size {PAGING_PAGE_SIZE} "
        f"returned {len(listed)} rows holding {len(distinct)} distinct CELEX, against "
        f"{expected} the endpoint counts over the same graph pattern: "
        f"{expected - len(distinct)} lost, {len(duplicated)} repeated {duplicated[:5]}"
    )
    assert listed == sorted(listed), "a keyset listing must arrive in identifier order"


def test_the_listing_costs_a_fraction_of_the_walk_it_replaces() -> None:
    """The claim rule 2.10 rests on, measured rather than asserted.

    A repair is only worth having if listing is genuinely cheaper than walking, so this
    compares the two over one window: how many requests each spends to establish which cases
    the window holds. Measured on 9 August 2026 the walk spent a count plus five pages
    against the listing's two — and over the whole of sector 6 the ratio is what matters:
    19,524 requests for the walk on 8 August, about 105 for the listing.
    """
    start, stop = datetime(2023, 4, 1, tzinfo=UTC), datetime(2023, 4, 2, tzinfo=UTC)
    walker = EurLexConnector(live_settings(pipeline_page_size=PAGING_PAGE_SIZE))
    lister = EurLexConnector(live_settings(eurlex_identifier_page_size=1000))
    try:
        walked = {candidate.source_id for candidate in walker.discover(start, stop)}
        walk_requests = walker.traffic.requests
        listed = {candidate.source_id for candidate in lister.enumerate_identifiers(start, stop)}
        list_requests = lister.traffic.requests
    finally:
        walker.close()
        lister.close()

    assert listed == walked, "the two routes must agree on what the window holds"
    assert list_requests < walk_requests, (
        f"the listing spent {list_requests} requests and the walk {walk_requests}; the repair "
        f"is only justified while the first number is the smaller one"
    )


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
