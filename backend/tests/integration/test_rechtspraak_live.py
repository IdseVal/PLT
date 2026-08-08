"""The Netherlands connector against the live ``data.rechtspraak.nl`` endpoints.

Opt-in: ``pytest -m integration``. Ordinary runs never reach these, so a court's endpoint is
not called by every developer on every commit and CI stays green when it is down for
maintenance.

What these cover that the recorded fixtures cannot is the *contract* — whether the endpoint
still behaves the way the connector assumes. Four of those assumptions were surprises when
they were first measured and would fail silently rather than loudly if they changed:
``modified`` is read in Dutch local time while the feed's ``updated`` is UTC, ``return``
accepts ``DOC`` and nothing else, ``atom:subtitle`` states the window's own total, and
``sort=ASC`` with ``from``/``max`` holds a page boundary steady across requests. A fixture
cannot notice any of them changing — least of all the last, because a fixture has no ordering
of its own to be unstable, which is exactly how the EU connector shipped a walk that silently
lost a sixth of its corpus.

The connector no longer *relies* on that last one: it narrows every window until the feed
answers it in a single request, so a page boundary is not where its correctness comes from.
Both are still tested here, and deliberately so. The narrowed walk is what production does
and has to be pinned against the feed's own count. The paged walk is the fallback for a
single second holding more than one request returns, and it is the only thing that can tell
us whether the ordering *is* in fact stable today — a fact about Rechtspraak's implementation
rather than a promise it makes, and one worth knowing the day it stops being true.

The whole module fetches a handful of documents and reads about a dozen small feed pages, all
at the configured request rate. It is meant to stay that size: proving a point about paging by
hammering a court is its own defect.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

import httpx
import pytest

from plt.config import Settings
from plt.db.models import DocumentType
from plt.pipeline.base import Candidate
from plt.pipeline.connectors.rechtspraak import (
    _ATOM,
    RechtspraakConnector,
    _parse,
    _source_timezone,
    _window_bound,
)
from tests.conftest import build_settings

pytestmark = pytest.mark.integration

#: A window in the recent past, small enough to be one page and old enough to be settled.
WINDOW_END = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
WINDOW_START = WINDOW_END - timedelta(hours=2)

#: Page size the paging-integrity walk uses. Chosen against the windows below rather than for
#: roundness: at 11, a page boundary falls *between* two entries sharing an ``updated``
#: timestamp in the first window (positions 10/11 and 43/44 as measured on 8 August 2026),
#: which is the only arrangement under which ``sort=ASC`` over a non-unique key can drop or
#: repeat a row. It still keeps each window to five or six requests.
PAGING_PAGE_SIZE: Final = 11

#: Windows the paging-integrity walk covers. Both are two hours wide and both hold at least
#: one shared ``updated`` timestamp — the precondition the test exists for; a window of
#: distinct timestamps would pass whatever the paging did. Two windows on different days,
#: because a single one that happens to page cleanly proves nothing about the next, and two
#: still cost the court about eleven requests.
PAGING_WINDOWS: Final = [
    pytest.param(
        datetime(2026, 6, 2, 10, 0, tzinfo=UTC),
        datetime(2026, 6, 2, 12, 0, tzinfo=UTC),
        id="2026-06-02",
    ),
    pytest.param(WINDOW_START, WINDOW_END, id="2026-06-01"),
]

#: The count the feed states in its ``subtitle``: ``Aantal gevonden ECLI's: 57``.
_REPORTED_TOTAL: Final = re.compile(r"(\d+)")

#: Without a timezone database the connector cannot render the window in Dutch local time and
#: deliberately requests a *wider* one, filtering the surplus out in Python. The walk and the
#: feed's total would then be counts of two different windows, and comparing them would fail
#: for a reason that has nothing to do with paging. Every supported environment has one — the
#: dev extra installs ``tzdata`` on Windows — so this says so rather than reporting a defect.
_LOCAL_TIME_AVAILABLE: Final = _source_timezone() is not None


@pytest.fixture
def live() -> Iterator[RechtspraakConnector]:
    """Return a connector pointed at the real endpoints, closed afterwards."""
    settings: Settings = build_settings(rechtspraak_page_size=5)
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


def reported_total(connector: RechtspraakConnector, since: datetime, until: datetime) -> int:
    """Return the number of ECLIs the feed itself says a window holds.

    Asked with ``max=1``, so the oracle costs one entry rather than a page, and composed
    through the connector's own ``modified`` rendering so the window is character-for-character
    the one the walk requests. It is deliberately *not* derived from the walk: an oracle a
    paging defect can influence is not an oracle.

    Args:
        connector: The connector, for its client, its settings and its politeness throttle.
        since: Inclusive lower bound of the window.
        until: Upper bound of the window.

    Returns:
        The count stated in the feed's ``atom:subtitle``.

    Raises:
        AssertionError: If the feed states no count, which would mean the subtitle this
            oracle reads had changed shape.
    """
    params: list[tuple[str, str]] = [("max", "1"), ("from", "0"), ("sort", "ASC")]
    if connector.settings.rechtspraak_documents_only:
        params.append(("return", "DOC"))
    params.append(("modified", _window_bound(since)))
    params.append(("modified", _window_bound(until)))
    response = connector._client.get(
        connector.settings.rechtspraak_search_url, params=httpx.QueryParams(tuple(params))
    )
    feed = _parse(response.content, described_as="a search feed", source_id="paging oracle")
    subtitle = (feed.findtext(f"{{{_ATOM}}}subtitle") or "").strip()
    stated = _REPORTED_TOTAL.search(subtitle)
    assert stated is not None, f"the feed no longer states a total in its subtitle: {subtitle!r}"
    return int(stated.group(1))


def assert_whole(
    found: Sequence[Candidate],
    expected: int,
    since: datetime,
    until: datetime,
    walked: str,
) -> None:
    """Assert a walk returned the window: nothing lost, nothing twice, and a tie in it.

    Args:
        found: What the walk returned.
        expected: What the feed says the window holds.
        since: Inclusive lower bound of the window.
        until: Upper bound of the window.
        walked: How the window was walked, for the failure message.

    Raises:
        AssertionError: If the window no longer holds a tie — in which case the check has
            gone vacuous and says so rather than passing — or if the walk lost or repeated
            an ECLI.
    """
    assert expected > PAGING_PAGE_SIZE, (
        f"the window {since:%Y-%m-%dT%H:%M} to {until:%Y-%m-%dT%H:%M} now holds {expected} "
        f"ECLIs, which is one page at a size of {PAGING_PAGE_SIZE}; it no longer tests paging "
        f"and needs re-picking"
    )
    shared = [moment for moment, seen in Counter(c.modified_at for c in found).items() if seen > 1]
    assert shared, (
        f"no two entries in {since:%Y-%m-%dT%H:%M} to {until:%Y-%m-%dT%H:%M} share an updated "
        f"timestamp any more, so this window no longer exercises the tie that makes sort=ASC "
        f"a partial order; pick one that does rather than leaving the test passing vacuously"
    )
    identifiers = [candidate.source_id for candidate in found]
    distinct = set(identifiers)
    duplicated = sorted(ecli for ecli, seen in Counter(identifiers).items() if seen > 1)
    lost = expected - len(distinct)
    # One assertion for both directions, so a failure reports the whole shape of the loss
    # rather than whichever half of it pytest reached first.
    assert not lost and not duplicated, (
        f"the {walked} walk of {since:%Y-%m-%dT%H:%M} to {until:%Y-%m-%dT%H:%M} at page size "
        f"{PAGING_PAGE_SIZE} returned {len(identifiers)} entries holding {len(distinct)} "
        f"distinct ECLIs, against {expected} the feed counts in the same window: {lost} lost "
        f"({lost / expected:.1%}), {len(duplicated)} returned more than once {duplicated[:5]}"
    )


@pytest.mark.skipif(
    not _LOCAL_TIME_AVAILABLE,
    reason="no timezone database, so the requested window is widened and the totals differ",
)
@pytest.mark.parametrize(("since", "until"), PAGING_WINDOWS)
def test_a_narrowed_walk_yields_every_ecli_the_feed_counts(
    since: datetime,
    until: datetime,
) -> None:
    """The union of the windows of a walk must be the range.

    This is what production does. The Rechtspraak feed cannot be paged by key — it offers
    ``from`` and ``max`` and nothing to page *after* — so instead of paging over an order the
    endpoint does not guarantee, the connector cuts the range into windows the feed answers
    whole. Splitting is where the risk moved to: a window boundary in the wrong place loses
    an entry just as surely as a page boundary did, and a walk that overlaps its windows
    returns one twice. So the same oracle answers both questions, against a count the walk
    cannot influence.

    The window is still required to hold a tie. Narrowing is supposed to make ties harmless —
    two entries sharing a timestamp cannot be split across a boundary, because a boundary is
    a whole second and so is a tie — and a check of that on a window with no ties in it would
    be a check of nothing.

    Args:
        since: Inclusive lower bound of the window.
        until: Upper bound of the window.
    """
    settings: Settings = build_settings(rechtspraak_page_size=PAGING_PAGE_SIZE)
    connector = RechtspraakConnector(settings)
    try:
        expected = reported_total(connector, since, until)
        found = list(connector.discover(since, until))
    finally:
        connector.close()

    assert_whole(found, expected, since, until, "narrowed")


@pytest.mark.skipif(
    not _LOCAL_TIME_AVAILABLE,
    reason="no timezone database, so the requested window is widened and the totals differ",
)
@pytest.mark.parametrize(("since", "until"), PAGING_WINDOWS)
def test_paging_one_window_by_offset_still_holds_its_order(
    since: datetime,
    until: datetime,
) -> None:
    """The fallback path, and the only measurement of whether the sort is stable today.

    A window at ``rechtspraak_min_window_seconds`` that still holds more than a page is paged
    by offset, because the alternative is dropping it. Forcing the floor up to the width of
    the whole window puts the connector on exactly that path, over a window whose page
    boundaries are known to fall between entries sharing an ``updated`` timestamp.

    A failure here is **not** a defect in the connector — production no longer walks this way
    — but it is the thing worth knowing: it would mean Rechtspraak had begun reordering ties
    between requests, that the corpus captured by offset before this change is suspect, and
    that the fallback needs to stop being a fallback.

    Args:
        since: Inclusive lower bound of the window.
        until: Upper bound of the window.
    """
    settings: Settings = build_settings(
        rechtspraak_page_size=PAGING_PAGE_SIZE,
        rechtspraak_min_window_seconds=int((until - since).total_seconds()),
        rechtspraak_window_days=(until - since).total_seconds() / 86400,
    )
    connector = RechtspraakConnector(settings)
    try:
        expected = reported_total(connector, since, until)
        found = list(connector.discover(since, until))
    finally:
        connector.close()

    assert_whole(found, expected, since, until, "paged")


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
