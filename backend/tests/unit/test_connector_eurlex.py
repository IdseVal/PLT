"""The EU connector, driven against recorded CELLAR payloads.

No test here touches the network (``CONTRIBUTING.md`` section 4): the SPARQL endpoint and
the CELLAR REST route are both served by :class:`FakeCellar` over an
:class:`httpx.MockTransport`, out of the fixtures in ``tests/fixtures/eurlex/``. Two of
those fixtures are recorded verbatim from the live endpoint — the Blaise judgment
(``62017CJ0616``, a glyphosate case) and the Bayer neonicotinoids judgment
(``62013TJ0429``) — and the rest are hand-written to pin the edges the live corpus does not
conveniently supply: a bare notice, a case published in no preferred language, loose legacy
HTML, and two XML attack payloads.

What the tests are for, in the order the issue asks for it: discovery stays under the
10,000-result cap by walking and halving date windows; one CELEX becomes one case however
many works and language versions stand behind it; language selection is configurable and
recorded; citations reach the citation table; queries cannot be injected into; missing
documents do not end a run; and the XML parsing resists XXE and entity expansion.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from plt.config import EurLexDiscoveryDate, Settings
from plt.db.models import DocumentType, LawDomain, PartyRole
from plt.pipeline.base import (
    Candidate,
    DocumentUnavailableError,
    NormalisedCase,
    RawDocument,
    SourceUnavailableError,
)
from plt.pipeline.connectors.eurlex import EurLexConnector
from plt.pipeline.filters.keywords import KeywordFilter
from plt.pipeline.http import PoliteClient
from plt.pipeline.registry import connector_for
from tests.conftest import build_settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "eurlex"

#: A glyphosate case, recorded from the live endpoint on 4 August 2026.
BLAISE = "62017CJ0616"

#: The neonicotinoids judgment of the General Court, recorded on the same day.
BAYER = "62013TJ0429"

SPARQL_URL = "https://sparql.invalid/sparql"
CELLAR_URL = "http://cellar.invalid/celex"

#: The two window bounds of a discovery query, in the order the FILTER states them.
_BOUNDS = re.compile(r'"([0-9T:Z .+-]+)"\^\^xsd:(?:dateTime|date)')


def fixture(name: str) -> str:
    """Return the contents of a recorded or hand-written fixture.

    Args:
        name: File name below ``tests/fixtures/eurlex``.

    Returns:
        The payload as text.
    """
    return (FIXTURES / name).read_text(encoding="utf-8")


def settings(**overrides: Any) -> Settings:  # noqa: ANN401 - arbitrary field overrides
    """Return test settings pointed at the fake endpoints.

    Args:
        **overrides: Fields to override on top of the fake endpoints.

    Returns:
        Validated settings.
    """
    defaults: dict[str, Any] = {
        "eurlex_sparql_url": SPARQL_URL,
        "eurlex_cellar_base_url": CELLAR_URL,
        # The politeness throttle is real code and is tested in test_pipeline_http; here it
        # would only make every test sleep.
        "http_requests_per_second": 100.0,
    }
    return build_settings(**{**defaults, **overrides})


class Row:
    """One CELEX number as the discovery query reports it.

    Attributes:
        celex: The CELEX number.
        modified_at: CELLAR's own last modification timestamp.
        document_date: Date of the decision.
        title: Expression title in the preferred language, ``#``-separated as CELLAR has it.
    """

    def __init__(
        self,
        celex: str,
        modified_at: str,
        document_date: str = "2019-10-01",
        title: str | None = None,
    ) -> None:
        """Build a row.

        Args:
            celex: The CELEX number.
            modified_at: Last modification timestamp.
            document_date: Date of the decision.
            title: Expression title, or ``None``.
        """
        self.celex = celex
        self.modified_at = modified_at
        self.document_date = document_date
        self.title = title

    @property
    def instant(self) -> datetime:
        """Return the modification timestamp as an instant."""
        return datetime.fromisoformat(self.modified_at).astimezone(UTC)

    def binding(self) -> dict[str, dict[str, str]]:
        """Return the row as a SPARQL JSON binding set."""
        binding = {
            "celex": {"type": "literal", "value": self.celex},
            "modified_at": {"type": "literal", "value": self.modified_at},
            "document_date": {"type": "literal", "value": self.document_date},
        }
        if self.title is not None:
            binding["title"] = {"type": "literal", "value": self.title}
        return binding


class FakeCellar:
    """The two CELLAR endpoints, over an :class:`httpx.MockTransport`.

    It records what it was asked for, so a test can assert that a window really was halved
    or that a manifestation really was requested in one language rather than another.

    Attributes:
        queries: SPARQL queries received, in order.
        requests: ``(url, accept, accept_language)`` of every REST request, in order.
    """

    def __init__(
        self,
        rows: list[Row] | None = None,
        notices: dict[str, str] | None = None,
        texts: dict[tuple[str, str], str] | None = None,
        *,
        content_type: str = "application/xhtml+xml;charset=UTF-8",
        redirect_notices: bool = False,
    ) -> None:
        """Build the fake source.

        Args:
            rows: What discovery finds, in any order.
            notices: Notice payload per CELEX number.
            texts: Manifestation payload per ``(celex, iso 639-3 code)``.
            content_type: Content type served for a manifestation.
            redirect_notices: Whether a notice request is answered with a redirect first, as
                the live route does.
        """
        self.rows = rows or []
        self.notices = notices or {}
        self.texts = texts or {}
        self.content_type = content_type
        self.redirect_notices = redirect_notices
        self.queries: list[str] = []
        self.requests: list[tuple[str, str, str | None]] = []

    def transport(self) -> httpx.MockTransport:
        """Return a transport serving this source."""
        return httpx.MockTransport(self.handle)

    def client(self, config: Settings) -> PoliteClient:
        """Return a polite client over this source.

        Args:
            config: Settings the client reads its politeness budget from.

        Returns:
            The client.
        """
        return PoliteClient(config, transport=self.transport())

    def connector(self, config: Settings | None = None) -> EurLexConnector:
        """Return a connector wired to this source.

        Args:
            config: Settings, defaulting to the fake endpoints.

        Returns:
            The connector.
        """
        resolved = config if config is not None else settings()
        return EurLexConnector(resolved, client=self.client(resolved))

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Answer one request.

        Args:
            request: The outgoing request.

        Returns:
            The response.
        """
        if request.method == "POST":
            return self._sparql(request)
        return self._rest(request)

    def _sparql(self, request: httpx.Request) -> httpx.Response:
        """Answer a SPARQL query.

        Args:
            request: The query request.

        Returns:
            A SPARQL JSON result set.
        """
        query = _form_field(request.content.decode("utf-8"), "query")
        self.queries.append(query)
        start, stop = window_of(query)
        inside = sorted(
            (row for row in self.rows if start <= row.instant < stop),
            key=lambda row: (row.instant, row.celex),
        )
        if "COUNT(DISTINCT ?celex)" in query:
            return _json({"total": {"type": "literal", "value": str(len(inside))}})
        limit, offset = _paging(query)
        page = inside[offset : offset + limit]
        return _json(*(row.binding() for row in page))

    def _celex_in(self, url: str) -> str:
        """Return the CELEX number a REST URL refers to.

        Args:
            url: The requested URL, which may be the redirect target rather than the
                original ``/celex/<CELEX>`` route.

        Returns:
            The CELEX number, or the last path segment when none is recognised.
        """
        for celex in (*self.notices, *(celex for celex, _ in self.texts)):
            if celex in url:
                return celex
        return url.rsplit("/", 1)[-1]

    def _rest(self, request: httpx.Request) -> httpx.Response:
        """Answer a CELLAR REST request.

        Args:
            request: The retrieval request.

        Returns:
            The notice, the manifestation, or a 404.
        """
        accept = request.headers.get("Accept", "")
        language = request.headers.get("Accept-Language")
        celex = self._celex_in(str(request.url))
        self.requests.append((str(request.url), accept, language))
        if "notice=object" in accept:
            payload = self.notices.get(celex)
            if payload is None:
                return httpx.Response(404, text="not found")
            if self.redirect_notices and "/cellar/" not in str(request.url):
                return httpx.Response(
                    302, headers={"Location": f"http://cellar.invalid/cellar/{celex}/xml/object"}
                )
            return httpx.Response(
                200, text=payload, headers={"content-type": "application/xml;notice=object"}
            )
        payload = self.texts.get((celex, (language or "").upper()))
        if payload is None:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=payload, headers={"content-type": self.content_type})


def _form_field(body: str, name: str) -> str:
    """Return one field of a form-encoded body.

    Args:
        body: The encoded body.
        name: Field name.

    Returns:
        The decoded value, or an empty string.
    """
    values = parse_qs(body).get(name, [])
    return values[0] if values else ""


def _json(*bindings: dict[str, dict[str, str]]) -> httpx.Response:
    """Return a SPARQL JSON result set.

    Args:
        *bindings: The binding sets.

    Returns:
        The response.
    """
    return httpx.Response(
        200,
        json={"head": {"vars": []}, "results": {"bindings": list(bindings)}},
        headers={"content-type": "application/sparql-results+json"},
    )


def window_of(query: str) -> tuple[datetime, datetime]:
    """Return the window a discovery query filters on.

    Args:
        query: The query text.

    Returns:
        The inclusive start and exclusive stop.

    Raises:
        AssertionError: If the query states no window, which would mean the connector had
            issued the unbounded query the result cap makes impossible.
    """
    bounds = _BOUNDS.findall(query)
    assert len(bounds) == 2, f"a discovery query must be bounded on both sides, got: {query}"
    return _instant(bounds[0]), _instant(bounds[1])


def _instant(value: str) -> datetime:
    """Parse a bound out of a query literal.

    Args:
        value: The literal's text.

    Returns:
        The instant, in UTC.
    """
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _paging(query: str) -> tuple[int, int]:
    """Return the ``LIMIT`` and ``OFFSET`` of a query.

    Args:
        query: The query text.

    Returns:
        The limit and the offset.
    """
    match = re.search(r"LIMIT (\d+) OFFSET (\d+)", query)
    assert match is not None, "a page query must be limited"
    return int(match.group(1)), int(match.group(2))


def rows(count: int, *, day: int = 1) -> list[Row]:
    """Build a run of discovery rows, one minute apart.

    Args:
        count: How many.
        day: Day of January 2026 they were modified on.

    Returns:
        The rows.
    """
    return [
        Row(
            celex=f"6202{index % 10}CJ{index:04d}",
            modified_at=f"2026-01-{day:02d}T00:{index % 60:02d}:00+00:00",
        )
        for index in range(count)
    ]


@pytest.fixture
def blaise_source() -> FakeCellar:
    """Return a fake CELLAR holding the recorded Blaise judgment in English."""
    return FakeCellar(
        rows=[Row(BLAISE, "2025-08-24T11:49:54+00:00", "2019-10-01")],
        notices={BLAISE: fixture(f"notice-{BLAISE}.xml")},
        texts={(BLAISE, "ENG"): fixture(f"text-{BLAISE}-eng.xhtml")},
    )


@pytest.fixture
def blaise_case(blaise_source: FakeCellar) -> Iterator[NormalisedCase]:
    """Return the recorded Blaise judgment, fetched and normalised."""
    with blaise_source.connector() as connector:
        candidate = Candidate(source_id=BLAISE, jurisdiction_code="EU")
        yield connector.normalise(connector.fetch(candidate))


# -- discovery -------------------------------------------------------------------------


def test_discovery_walks_windows_rather_than_issuing_one_query() -> None:
    source = FakeCellar(rows=rows(3, day=5))

    with source.connector(settings(eurlex_window_days=1)) as connector:
        found = list(
            connector.discover(
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 8, tzinfo=UTC),
            )
        )

    assert [candidate.source_id for candidate in found] == [row.celex for row in rows(3, day=5)]
    # Seven days, one day at a time: every query is bounded, and no window is wider than the
    # configured width.
    windows = [window_of(query) for query in source.queries]
    assert windows
    assert all(stop - start <= timedelta(days=1) for start, stop in windows)


def test_a_window_that_reaches_the_cap_is_halved_until_it_fits() -> None:
    """The 10,000-result cap is the reason discovery pages by date at all."""
    # 40 rows in the first six hours of the window, 4 elsewhere, and a cap of 8: the walk has
    # to narrow the window around the dense stretch.
    dense = [
        Row(f"62026CJ{index:04d}", f"2026-01-01T0{index % 6}:00:00+00:00") for index in range(40)
    ]
    sparse = [Row(f"62026CO{index:04d}", f"2026-01-1{index}T12:00:00+00:00") for index in range(4)]
    source = FakeCellar(rows=dense + sparse)

    with source.connector(
        settings(eurlex_max_results=8, eurlex_window_days=30, eurlex_min_window_seconds=900)
    ) as connector:
        found = list(
            connector.discover(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC))
        )

    counted = [query for query in source.queries if "COUNT(DISTINCT ?celex)" in query]
    paged = [query for query in source.queries if "COUNT(DISTINCT ?celex)" not in query]
    # Every window that was actually paged came in under the cap.
    assert paged, "the walk paged no window at all"
    for query in paged:
        start, stop = window_of(query)
        assert sum(1 for row in dense + sparse if start <= row.instant < stop) < 8
    assert len(counted) > len(paged), "a window was paged without being counted first"
    assert len(found) == len(dense) + len(sparse)


def test_a_window_at_the_floor_is_processed_rather_than_split_forever(
    caplog: pytest.LogCaptureFixture,
) -> None:
    packed = [Row(f"62026CJ{index:04d}", "2026-01-01T00:00:00+00:00") for index in range(12)]
    source = FakeCellar(rows=packed)

    with (
        caplog.at_level("WARNING"),
        source.connector(
            settings(eurlex_max_results=4, eurlex_window_days=1, eurlex_min_window_seconds=3600)
        ) as connector,
    ):
        found = list(
            connector.discover(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
        )

    assert len(found) == 12
    assert any("result cap" in record.message for record in caplog.records)


def test_discovery_pages_through_a_window() -> None:
    source = FakeCellar(rows=rows(25, day=3))

    with source.connector(settings(pipeline_page_size=10, eurlex_window_days=30)) as connector:
        found = list(
            connector.discover(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC))
        )

    assert len(found) == 25
    offsets = [
        _paging(query)[1] for query in source.queries if "COUNT(DISTINCT ?celex)" not in query
    ]
    assert offsets == [0, 10, 20]


def test_discovery_streams_rather_than_materialising_the_window() -> None:
    source = FakeCellar(rows=rows(30, day=2))

    with source.connector(settings(pipeline_page_size=10)) as connector:
        stream = connector.discover(
            datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
        )
        first = next(stream)

    assert first.source_id == rows(30, day=2)[0].celex
    # One count and one page, not the whole window: the runner consumes this lazily and
    # commits per batch, which is what keeps memory flat over a run of any size.
    assert len(source.queries) == 2


def test_a_candidate_carries_what_the_runner_needs() -> None:
    source = FakeCellar(
        rows=[
            Row(
                BLAISE,
                "2025-08-24T11:49:54+02:00",
                "2019-10-01",
                title="Judgment of the Court.#Blaise and Others.#Case C-616/17.",
            )
        ]
    )

    with source.connector() as connector:
        [candidate] = list(
            connector.discover(datetime(2025, 8, 1, tzinfo=UTC), datetime(2025, 9, 1, tzinfo=UTC))
        )

    assert candidate.source_id == BLAISE
    assert candidate.jurisdiction_code == "EU"
    assert candidate.modified_at == datetime(2025, 8, 24, 9, 49, 54, tzinfo=UTC)
    # CELLAR's own revision marker, so an unchanged case is skipped without being fetched.
    assert candidate.content_hash == "2025-08-24T11:49:54+02:00"
    assert candidate.decision_date == date(2019, 10, 1)
    assert candidate.source_url == f"{CELLAR_URL}/{BLAISE}"
    assert candidate.title is not None
    assert "#" not in candidate.title
    assert "Blaise" in candidate.title
    assert candidate.cursor is not None


def test_candidates_are_yielded_oldest_first() -> None:
    source = FakeCellar(rows=rows(12, day=4))

    with source.connector(settings(pipeline_page_size=5)) as connector:
        found = list(
            connector.discover(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC))
        )

    stamps = [candidate.modified_at for candidate in found]
    assert stamps == sorted(stamp for stamp in stamps if stamp is not None)


def test_a_row_without_a_celex_number_is_skipped_rather_than_ending_the_run() -> None:
    source = FakeCellar(rows=[Row("", "2026-01-01T00:00:00+00:00"), *rows(1, day=1)])

    with source.connector() as connector:
        found = list(
            connector.discover(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
        )

    assert [candidate.source_id for candidate in found] == [rows(1, day=1)[0].celex]


def test_document_mode_bounds_the_decision_date_and_leaves_the_checkpoint_alone() -> None:
    source = FakeCellar(rows=rows(2, day=6))

    with source.connector(
        settings(eurlex_discovery_date=EurLexDiscoveryDate.DOCUMENT, eurlex_window_days=30)
    ) as connector:
        found = list(
            connector.discover(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC))
        )

    assert all("work_date_document" in query for query in source.queries)
    assert all("xsd:date)" in query for query in source.queries)
    # A backfill by decision date says nothing about what has changed since, so it must not
    # push the incremental checkpoint forward.
    assert [candidate.modified_at for candidate in found] == [None, None]
    assert all(candidate.content_hash for candidate in found)


def test_modification_mode_bounds_the_repository_timestamp() -> None:
    source = FakeCellar(rows=rows(1, day=1))

    with source.connector() as connector:
        list(connector.discover(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)))

    assert all("lastModificationDate" in query for query in source.queries)
    assert all("xsd:dateTime)" in query for query in source.queries)


def test_an_empty_window_asks_the_source_nothing() -> None:
    source = FakeCellar()

    with source.connector() as connector:
        found = list(
            connector.discover(datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
        )

    assert found == []
    assert source.queries == []


def test_an_unreadable_result_set_ends_the_run_rather_than_looking_empty() -> None:
    """A half-enumerated window must never be mistaken for an exhausted one."""

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, text="<html>maintenance</html>")

    config = settings()
    connector = EurLexConnector(
        config, client=PoliteClient(config, transport=httpx.MockTransport(handle))
    )

    with connector, pytest.raises(SourceUnavailableError, match="unreadable result set"):
        list(connector.discover(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)))


def test_a_rejected_query_ends_the_run() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, text="malformed query")

    config = settings()
    connector = EurLexConnector(
        config, client=PoliteClient(config, transport=httpx.MockTransport(handle))
    )

    with connector, pytest.raises(SourceUnavailableError, match="rejected a discovery query"):
        list(connector.discover(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)))


# -- query construction and injection ---------------------------------------------------


def test_the_discovery_query_is_built_from_typed_literals_only() -> None:
    source = FakeCellar(rows=[])

    with source.connector() as connector:
        list(connector.discover(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)))

    query = source.queries[0]
    # The only quoted strings in the query are the sector number and the two window bounds,
    # and every one of them is typed. Nothing else is interpolated at all.
    literals = re.findall(r'"[^"]*"(\^\^xsd:[A-Za-z]+)?', query)
    assert literals
    assert all(literal for literal in literals), f"an untyped literal reached the query: {query}"


@pytest.mark.parametrize(
    "code",
    ["ENG> } UNION { ?s ?p ?o", 'ENG"', "ENG/../../secret", "ENG ?x"],
)
def test_a_vocabulary_code_that_could_break_out_of_a_uri_is_refused(code: str) -> None:
    """The codes are the only configuration that ends up inside a query."""
    with pytest.raises(ValueError, match="vocabulary code"):
        settings(eurlex_languages=[code])


@pytest.mark.parametrize(
    "identifier",
    [
        "../../../etc/passwd",
        "62017CJ0616/../../secret",
        '62017CJ0616" }',
        "62017CJ0616 OR 1=1",
        "",
        "62017\nCJ0616",
        "62017CJ 0616",
    ],
)
def test_a_malformed_identifier_never_reaches_a_url(identifier: str) -> None:
    source = FakeCellar(notices={BLAISE: fixture(f"notice-{BLAISE}.xml")})

    with source.connector() as connector, pytest.raises(DocumentUnavailableError):
        connector.fetch(Candidate(source_id=identifier or "x", jurisdiction_code="EU"))

    assert source.requests == []


# -- fetching --------------------------------------------------------------------------


def test_fetch_keeps_the_notice_verbatim(blaise_source: FakeCellar) -> None:
    with blaise_source.connector() as connector:
        raw = connector.fetch(Candidate(source_id=BLAISE, jurisdiction_code="EU"))

    assert isinstance(raw, RawDocument)
    assert raw.payload == fixture(f"notice-{BLAISE}.xml")
    assert raw.media_format == "xml"
    accepts = [accept for _, accept, _ in blaise_source.requests]
    assert accepts[0] == "application/xml;notice=object"


def test_fetch_asks_for_the_preferred_language(blaise_source: FakeCellar) -> None:
    with blaise_source.connector() as connector:
        raw = connector.fetch(Candidate(source_id=BLAISE, jurisdiction_code="EU"))

    languages = [language for _, _, language in blaise_source.requests if language]
    assert languages == ["eng"]
    [manifestation] = raw.source_metadata["manifestations"]
    assert manifestation["selected_as"] == "preferred"
    assert manifestation["media_format"] == "xhtml"


def test_a_missing_notice_is_reported_as_one_document_not_as_an_outage() -> None:
    source = FakeCellar()

    with source.connector() as connector, pytest.raises(DocumentUnavailableError):
        connector.fetch(Candidate(source_id="62099CJ9999", jurisdiction_code="EU"))


def test_a_missing_manifestation_does_not_lose_the_case() -> None:
    source = FakeCellar(notices={BLAISE: fixture(f"notice-{BLAISE}.xml")}, texts={})

    with source.connector() as connector:
        raw = connector.fetch(Candidate(source_id=BLAISE, jurisdiction_code="EU"))
        case = connector.normalise(raw)

    assert raw.source_metadata["manifestations"] == []
    # The notice is still kept, so the case is stored with its metadata and no full text.
    assert len(case.documents) == 1
    assert case.documents[0].raw_payload == fixture(f"notice-{BLAISE}.xml")
    assert case.full_text is None


def test_a_redirect_to_the_cellar_uri_is_followed() -> None:
    source = FakeCellar(
        notices={BLAISE: fixture(f"notice-{BLAISE}.xml")},
        texts={(BLAISE, "ENG"): fixture(f"text-{BLAISE}-eng.xhtml")},
        redirect_notices=True,
    )

    with source.connector() as connector:
        raw = connector.fetch(Candidate(source_id=BLAISE, jurisdiction_code="EU"))

    assert raw.payload.startswith("<?xml")
    assert "/cellar/" in (raw.source_url or "")


def test_every_request_identifies_the_project(blaise_source: FakeCellar) -> None:
    seen: list[str] = []
    inner = blaise_source.handle

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("User-Agent", ""))
        return inner(request)

    config = settings()
    connector = EurLexConnector(
        config, client=PoliteClient(config, transport=httpx.MockTransport(record))
    )
    with connector:
        connector.fetch(Candidate(source_id=BLAISE, jurisdiction_code="EU"))

    assert seen
    assert all("wur.nl" in agent and "PLT/" in agent for agent in seen)


# -- language selection ------------------------------------------------------------------


def test_the_preferred_language_wins_and_is_recorded(blaise_case: NormalisedCase) -> None:
    [text] = [document for document in blaise_case.documents if document.has_text]

    assert text.language == "en"
    assert text.source_metadata["cellar_language"] == "ENG"
    assert text.source_metadata["selected_as"] == "preferred"
    assert blaise_case.language == "en"


def test_a_case_without_a_preferred_language_falls_back_to_the_procedural_one() -> None:
    source = FakeCellar(
        notices={"61987CO0302": fixture("notice-french-only.xml")},
        texts={("61987CO0302", "FRA"): "<html><body><p>Ordonnance</p></body></html>"},
        content_type="text/html;charset=UTF-8",
    )

    with source.connector() as connector:
        case = connector.normalise(
            connector.fetch(Candidate(source_id="61987CO0302", jurisdiction_code="EU"))
        )

    languages = [language for _, _, language in source.requests if language]
    assert languages == ["fra"]
    [text] = [document for document in case.documents if document.has_text]
    assert text.language == "fr"
    assert text.source_metadata["selected_as"] == "procedural"
    assert case.language == "fr"


def test_several_configured_languages_become_several_documents_on_one_case() -> None:
    source = FakeCellar(
        notices={BLAISE: fixture(f"notice-{BLAISE}.xml")},
        texts={
            (BLAISE, "ENG"): fixture(f"text-{BLAISE}-eng.xhtml"),
            (BLAISE, "NLD"): "<html><body><p>gewasbeschermingsmiddelen</p></body></html>",
        },
    )

    with source.connector(settings(eurlex_languages=["eng", "nld"])) as connector:
        case = connector.normalise(
            connector.fetch(Candidate(source_id=BLAISE, jurisdiction_code="EU"))
        )

    texts = [document for document in case.documents if document.has_text]
    assert [document.language for document in texts] == ["en", "nl"]
    # One case, whatever the number of language versions - and a term in any of them is
    # visible to the filter chain, which is the whole point of joining them.
    assert case.source_id == BLAISE
    assert "gewasbeschermingsmiddelen" in (case.full_text or "")
    assert "plant protection products" in (case.full_text or "").lower()


def test_a_language_the_notice_does_not_list_is_never_requested() -> None:
    source = FakeCellar(
        notices={"61987CO0302": fixture("notice-french-only.xml")},
        texts={("61987CO0302", "DEU"): "<html><body><p>Beschluss</p></body></html>"},
        content_type="text/html;charset=UTF-8",
    )

    with source.connector(settings(eurlex_languages=["eng"])) as connector:
        connector.fetch(Candidate(source_id="61987CO0302", jurisdiction_code="EU"))

    languages = [language for _, _, language in source.requests if language]
    assert "eng" not in languages


# -- normalisation ------------------------------------------------------------------------


def test_one_celex_becomes_one_case_with_its_languages_as_documents() -> None:
    """The acceptance criterion: language manifestations are documents, never cases."""
    source = FakeCellar(
        notices={BLAISE: fixture(f"notice-{BLAISE}.xml")},
        texts={
            (BLAISE, "ENG"): fixture(f"text-{BLAISE}-eng.xhtml"),
            (BLAISE, "FRA"): "<html><body><p>produits phytopharmaceutiques</p></body></html>",
            (BLAISE, "DEU"): "<html><body><p>Pflanzenschutzmittel</p></body></html>",
        },
    )

    with source.connector(settings(eurlex_languages=["eng", "fra", "deu"])) as connector:
        cases = [
            connector.normalise(
                connector.fetch(Candidate(source_id=BLAISE, jurisdiction_code="EU"))
            )
        ]

    assert {case.source_id for case in cases} == {BLAISE}
    [case] = cases
    # Three language versions plus the notice: four documents, one case.
    assert len(case.documents) == 4
    assert sorted(document.language for document in case.documents if document.language) == [
        "de",
        "en",
        "fr",
    ]


def test_the_notice_is_stored_as_its_own_document(blaise_case: NormalisedCase) -> None:
    [notice] = [
        document
        for document in blaise_case.documents
        if document.media_format == "xml" and not document.has_text
    ]

    assert notice.doc_type is DocumentType.OTHER
    assert notice.raw_payload == fixture(f"notice-{BLAISE}.xml")
    # Kept verbatim, but never scanned: the filter chain reads judgments, not markup.
    assert notice.full_text is None


def test_the_recorded_judgment_maps_onto_the_schema(blaise_case: NormalisedCase) -> None:
    assert blaise_case.source_id == BLAISE
    assert blaise_case.jurisdiction_code == "EU"
    assert blaise_case.source_system == "cellar"
    assert blaise_case.decision_date == date(2019, 10, 1)
    assert blaise_case.case_numbers == ("C-616/17",)
    assert blaise_case.procedure_type == "Reference for a preliminary ruling"
    assert blaise_case.title is not None
    assert "Blaise" in blaise_case.title
    assert blaise_case.court is not None
    assert blaise_case.court.name == "Court of Justice"
    assert blaise_case.court.source_identifier.endswith("/corporate-body/CJ")
    assert blaise_case.source_metadata["ecli"] == "ECLI:EU:C:2019:800"
    assert blaise_case.source_metadata["celex"] == BLAISE
    assert "cellar/1c5d848d" in blaise_case.source_metadata["cellar_uri"]
    assert blaise_case.source_metadata["procedure_language"] == "FRA"
    assert len(blaise_case.source_metadata["available_languages"]) == 23


def test_the_subject_carries_the_descriptors_the_keyword_list_scores(
    blaise_case: NormalisedCase,
) -> None:
    assert blaise_case.subject is not None
    assert "Plant protection products" in blaise_case.subject
    assert blaise_case.source_metadata["case_law_directory_codes"]


def test_the_law_domain_is_left_null_rather_than_guessed(blaise_case: NormalisedCase) -> None:
    """A wrong classification is worse than a missing one in a research database."""
    assert blaise_case.law_domain is None
    assert blaise_case.law_subfield is None
    assert LawDomain.PUBLIC not in {blaise_case.law_domain}


def test_a_direct_action_names_its_parties() -> None:
    source = FakeCellar(notices={BAYER: fixture(f"notice-{BAYER}.xml")})

    with source.connector() as connector:
        case = connector.normalise(
            connector.fetch(Candidate(source_id=BAYER, jurisdiction_code="EU"))
        )

    assert [(party.name, party.role) for party in case.parties] == [
        ("Bayer CropScience AG and Others", PartyRole.APPLICANT),
        ("European Commission", PartyRole.DEFENDANT),
    ]
    assert case.court is not None
    assert case.court.name == "General Court"
    # Joined cases are filed under a combined identifier as well; both numbers are searchable.
    assert case.case_numbers == ("T-429/13", "T-451/13")


def test_a_title_that_names_no_two_sides_keeps_one_party_with_no_invented_role(
    blaise_case: NormalisedCase,
) -> None:
    [party] = blaise_case.parties

    assert party.role is PartyRole.OTHER
    assert party.name.startswith("Criminal proceedings against")


def test_citations_reach_the_citation_table(blaise_case: NormalisedCase) -> None:
    targets = {citation.target_identifier: citation for citation in blaise_case.citations}

    # The instrument the case is about: Regulation (EC) No 1107/2009.
    assert "32009R1107" in targets
    assert targets["32009R1107"].target_scheme == "celex"
    # And the case law it cites.
    assert "62009CJ0077" in targets
    assert all(citation.citation_type for citation in blaise_case.citations)
    assert len({(c.target_identifier, c.citation_type) for c in blaise_case.citations}) == len(
        blaise_case.citations
    )


def test_the_citation_relation_cellar_recorded_is_preserved() -> None:
    source = FakeCellar(notices={BAYER: fixture(f"notice-{BAYER}.xml")})

    with source.connector() as connector:
        case = connector.normalise(
            connector.fetch(Candidate(source_id=BAYER, jurisdiction_code="EU"))
        )

    relations = {citation.citation_type for citation in case.citations}
    assert relations
    assert relations <= {
        "cites",
        "interprets",
        "declares_valid",
        "declares_void",
        "applies",
        "based_on",
        "incorporates",
    }


def test_a_bare_notice_normalises_without_raising() -> None:
    source = FakeCellar(notices={"61962CO0026": fixture("notice-minimal.xml")})

    with source.connector() as connector:
        case = connector.normalise(
            connector.fetch(Candidate(source_id="61962CO0026", jurisdiction_code="EU"))
        )

    assert case.source_id == "61962CO0026"
    assert case.court is None
    assert case.citations == ()
    assert case.parties == ()
    assert case.subject is None
    assert case.decision_date == date(1962, 11, 7)


def test_a_notice_that_is_not_xml_is_scoped_to_the_document() -> None:
    source = FakeCellar(notices={"62017CJ0616": "<NOTICE><WORK>"})

    with source.connector() as connector, pytest.raises(DocumentUnavailableError, match="XML"):
        connector.fetch(Candidate(source_id=BLAISE, jurisdiction_code="EU"))


def test_a_notice_without_a_work_is_scoped_to_the_document() -> None:
    raw = RawDocument(
        candidate=Candidate(source_id=BLAISE, jurisdiction_code="EU"),
        payload='<?xml version="1.0"?><NOTICE type="object"></NOTICE>',
        media_format="xml",
    )

    with (
        FakeCellar().connector() as connector,
        pytest.raises(DocumentUnavailableError, match="no WORK"),
    ):
        connector.normalise(raw)


# -- text extraction and XML hardening ----------------------------------------------------


def test_the_full_text_is_readable_and_free_of_markup(blaise_case: NormalisedCase) -> None:
    text = blaise_case.full_text or ""

    assert len(text) > 20_000
    assert "plant protection products" in text.lower()
    assert "glyphosate" in text.lower()
    assert "<p" not in text
    assert "  " not in text


def test_legacy_html_falls_back_to_the_html_parser() -> None:
    source = FakeCellar(
        notices={"61987CO0302": fixture("notice-french-only.xml")},
        texts={("61987CO0302", "FRA"): fixture("manifestation-legacy.html")},
        content_type="text/html;charset=UTF-8",
    )

    with source.connector() as connector:
        case = connector.normalise(
            connector.fetch(Candidate(source_id="61987CO0302", jurisdiction_code="EU"))
        )

    text = case.full_text or ""
    assert "Placing of plant protection products on the market" in text
    assert "Regulation (EC) No 1107/2009 & glyphosate" in text
    # Script and style content is not part of the judgment.
    assert "tracking" not in text
    assert "color: black" not in text


def test_an_external_entity_in_a_notice_is_not_resolved() -> None:
    source = FakeCellar(notices={BLAISE: fixture("notice-external-entity.xml")})

    with source.connector() as connector:
        try:
            case = connector.normalise(
                connector.fetch(Candidate(source_id=BLAISE, jurisdiction_code="EU"))
            )
        except DocumentUnavailableError:
            return  # Refusing the document outright is the other acceptable outcome.

    assert "root:" not in str(case.source_metadata)
    assert not (case.source_metadata.get("national_judgement") or "").strip()


def test_an_entity_expansion_bomb_is_not_expanded() -> None:
    source = FakeCellar(notices={BLAISE: fixture("notice-entity-expansion.xml")})

    with source.connector() as connector:
        try:
            case = connector.normalise(
                connector.fetch(Candidate(source_id=BLAISE, jurisdiction_code="EU"))
            )
        except DocumentUnavailableError:
            return

    assert len(str(case.source_metadata.get("national_judgement") or "")) < 1000


def test_an_external_entity_in_a_manifestation_is_not_resolved() -> None:
    bomb = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE html [<!ENTITY secret SYSTEM "file:///etc/passwd">]>'
        "<html><body><p>&secret;</p></body></html>"
    )
    source = FakeCellar(
        notices={"61987CO0302": fixture("notice-french-only.xml")},
        texts={("61987CO0302", "FRA"): bomb},
    )

    with source.connector() as connector:
        case = connector.normalise(
            connector.fetch(Candidate(source_id="61987CO0302", jurisdiction_code="EU"))
        )

    assert "root:" not in (case.full_text or "")


# -- the stages after this one -------------------------------------------------------------


def test_the_registry_serves_the_eu_with_this_connector() -> None:
    connector = connector_for("EU", settings())

    try:
        assert isinstance(connector, EurLexConnector)
        assert connector.name == "eurlex"
    finally:
        connector.close()


def test_a_recorded_pesticide_judgment_passes_the_curated_eu_list(
    blaise_case: NormalisedCase,
) -> None:
    """End to end for the two work streams that have to meet: connector and keyword list."""
    stage = KeywordFilter.for_jurisdiction("EU", settings=build_settings())

    result = stage.evaluate(blaise_case)

    assert result.passed, result.reason
    matched = {match.term_id for match in result.matches}
    assert "en-reg-1107-2009" in matched
    assert "en-glyphosate" in matched


def test_the_connector_closes_the_client_it_built() -> None:
    connector = EurLexConnector(settings())
    client = connector._client

    connector.close()

    with pytest.raises(RuntimeError):
        client.get("http://cellar.invalid/celex/62017CJ0616")
