"""The Netherlands connector, driven entirely off recorded responses.

Every payload under ``tests/fixtures/rechtspraak/`` came from ``data.rechtspraak.nl`` itself
and is served back through an :class:`httpx.MockTransport`, so nothing here touches the
network and the assertions are about what the endpoint really returns rather than about what
it was hoped to return. The two attack payloads and ``multivalued.xml`` are the exceptions and
are marked as hand-built in the fixtures README: no court publishes a billion-laughs bomb, and
a document repeating every repeatable field at once is rarer than it should be for a test.

The field-by-field assertion is on ``ECLI:NL:CBB:2024:147`` — a College van Beroep voor het
bedrijfsleven judgment, which is the court that hears Ctgb authorisation appeals and therefore
exactly the shape of document the tracker exists for.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from plt.config import Settings
from plt.db.models import DocumentType, LawDomain
from plt.pipeline.base import (
    Candidate,
    DocumentUnavailableError,
    NormalisedCase,
    RawDocument,
    SourceUnavailableError,
)
from plt.pipeline.connectors.rechtspraak import RechtspraakConnector
from plt.pipeline.filters.keywords import KeywordFilter
from plt.pipeline.http import PoliteClient
from tests.conftest import REPO_ROOT, build_settings
from tests.fakes import FakeConnector

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rechtspraak"

SEARCH_URL = "https://data.rechtspraak.nl/uitspraken/zoeken"
CONTENT_URL = "https://data.rechtspraak.nl/uitspraken/content"
VOCABULARY_URL = "https://data.rechtspraak.nl/Waardelijst"

#: The three ECLIs of ``search-page-1.atom``, in the order the feed lists them.
PAGE_ONE = (
    "ECLI:NL:RBDHA:2026:14464",
    "ECLI:NL:RBDHA:2026:12003",
    "ECLI:NL:RBDHA:2026:12044",
)


def fixture(name: str) -> bytes:
    """Return one recorded payload, exactly as it was captured.

    Args:
        name: File name under ``tests/fixtures/rechtspraak``.

    Returns:
        The payload bytes, byte-order mark and all.
    """
    return (FIXTURES / name).read_bytes()


class Endpoint:
    """A stand-in for ``data.rechtspraak.nl`` that serves recorded payloads.

    It records every request, so a test can assert on the query the connector composed —
    which is where the endpoint's two surprises live: ``modified`` in Dutch local time, and
    ``return=DOC``.
    """

    def __init__(
        self,
        documents: dict[str, bytes] | None = None,
        pages: list[bytes] | None = None,
        vocabulary: bytes | None = None,
    ) -> None:
        """Configure what the endpoint serves.

        Args:
            documents: Payload per ECLI. An ECLI that is absent answers 404, as the live
                endpoint does.
            pages: Search pages, served in order and then repeated from the last one.
            vocabulary: The ``Instanties`` payload, or ``None`` to answer 503.
        """
        self.documents = documents or {}
        self.pages = pages or [fixture("search-page-empty.atom")]
        self.vocabulary = vocabulary
        self.requests: list[httpx.Request] = []
        self._page_index = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one request.

        Args:
            request: The request the connector sent.

        Returns:
            The recorded response.
        """
        self.requests.append(request)
        path = str(request.url).split("?")[0]
        if path == SEARCH_URL:
            page = self.pages[min(self._page_index, len(self.pages) - 1)]
            self._page_index += 1
            return httpx.Response(200, content=page, headers={"Content-Type": "application/xml"})
        if path.endswith("/Instanties"):
            if self.vocabulary is None:
                return httpx.Response(503)
            return httpx.Response(200, content=self.vocabulary)
        identifier = request.url.params.get("id", "")
        payload = self.documents.get(identifier)
        if payload is None:
            return httpx.Response(404, json={"title": "Not Found"})
        return httpx.Response(200, content=payload, headers={"Content-Type": "application/xml"})

    @property
    def searches(self) -> list[httpx.QueryParams]:
        """Return the query parameters of every search request, in order."""
        return [
            request.url.params
            for request in self.requests
            if str(request.url).startswith(SEARCH_URL)
        ]

    @property
    def fetched(self) -> list[str]:
        """Return the identifiers every content request asked for, in order."""
        return [
            request.url.params["id"]
            for request in self.requests
            if str(request.url).startswith(CONTENT_URL)
        ]


def settings_for(**overrides: object) -> Settings:
    """Return test settings pointing at the live URLs the mock transport intercepts.

    Args:
        **overrides: Field values to override.

    Returns:
        Validated settings. The request rate is raised and the sleep is stubbed out in
        :func:`connector`, so a test never spends real time being polite to a mock.
    """
    return build_settings(
        rechtspraak_search_url=SEARCH_URL,
        rechtspraak_content_url=CONTENT_URL,
        rechtspraak_vocabulary_url=VOCABULARY_URL,
        keywords_dir=REPO_ROOT / "data" / "keywords",
        **overrides,
    )


def build(endpoint: Endpoint, **overrides: object) -> RechtspraakConnector:
    """Build a connector wired to a fake endpoint.

    Args:
        endpoint: The endpoint to serve responses from.
        **overrides: Settings overrides.

    Returns:
        The connector. It still fetches through
        :class:`~plt.pipeline.http.PoliteClient`, so the throttle, the retries and the
        ``User-Agent`` are exercised rather than bypassed.
    """
    settings = settings_for(**overrides)
    client = PoliteClient(
        settings,
        transport=httpx.MockTransport(endpoint),
        sleep=lambda _: None,
    )
    return RechtspraakConnector(settings, client=client)


@pytest.fixture
def cbb() -> Iterator[RechtspraakConnector]:
    """Return a connector serving the recorded CBb judgment and the court vocabulary."""
    endpoint = Endpoint(
        documents={"ECLI:NL:CBB:2024:147": fixture("ECLI_NL_CBB_2024_147.xml")},
        vocabulary=fixture("instanties.xml"),
    )
    connector = build(endpoint)
    try:
        yield connector
    finally:
        connector.close()


def candidate_for(identifier: str, **overrides: object) -> Candidate:
    """Return a candidate standing in for one discovery result.

    Args:
        identifier: The ECLI.
        **overrides: Fields to override.

    Returns:
        The candidate.
    """
    fields: dict[str, object] = {
        "source_id": identifier,
        "jurisdiction_code": "NL",
        "modified_at": datetime(2024, 3, 5, 12, 14, 6, tzinfo=UTC),
        "source_url": f"https://uitspraken.rechtspraak.nl/details?id={identifier}",
        "title": "feed title",
    }
    fields.update(overrides)
    return Candidate(**fields)  # type: ignore[arg-type]


def normalise(connector: RechtspraakConnector, identifier: str) -> NormalisedCase:
    """Fetch and normalise one document through a connector.

    Args:
        connector: The connector to drive.
        identifier: The ECLI to process.

    Returns:
        The normalised case.
    """
    return connector.normalise(connector.fetch(candidate_for(identifier)))


# -- Discovery ------------------------------------------------------------------------


def test_discovery_yields_the_feed_entries_oldest_first() -> None:
    endpoint = Endpoint(pages=[fixture("search-page-1.atom"), fixture("search-page-empty.atom")])
    connector = build(endpoint, pipeline_page_size=3)
    try:
        found = list(connector.discover(None, None))
    finally:
        connector.close()

    assert [candidate.source_id for candidate in found] == list(PAGE_ONE)
    moments = [candidate.modified_at for candidate in found]
    assert moments == sorted(moment for moment in moments if moment is not None)
    assert all(moment is not None and moment.tzinfo is not None for moment in moments)


def test_a_candidate_carries_what_the_pre_fetch_check_needs() -> None:
    endpoint = Endpoint(pages=[fixture("search-page-1.atom"), fixture("search-page-empty.atom")])
    connector = build(endpoint, pipeline_page_size=3)
    try:
        first = next(iter(connector.discover(None, None)))
    finally:
        connector.close()

    assert first.source_id == PAGE_ONE[0]
    # The Atom ``updated`` is hashed into the pipeline's own hash space, so an unchanged
    # document is skipped without being downloaded at all.
    assert first.content_hash is not None
    assert len(first.content_hash) == 64
    assert first.cursor == "0"
    assert first.source_url is not None
    assert first.source_url.startswith("https://uitspraken.rechtspraak.nl/details?id=")
    assert first.title is not None and first.title.startswith(PAGE_ONE[0])


def test_the_window_is_sent_in_dutch_local_time_and_restricted_to_documents() -> None:
    endpoint = Endpoint(pages=[fixture("search-page-empty.atom")])
    connector = build(endpoint, pipeline_page_size=7)
    try:
        list(connector.discover(datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 7, 1, tzinfo=UTC)))
    finally:
        connector.close()

    query = endpoint.searches[0]
    # June is CEST, so midnight UTC is two o'clock in Amsterdam. Sending the UTC value would
    # silently shift the window by two hours in a source that ignores an offset suffix.
    assert query.get_list("modified") == ["2026-06-01T02:00:00", "2026-07-01T02:00:00"]
    assert query["sort"] == "ASC"
    assert query["return"] == "DOC"
    assert query["max"] == "7"
    assert query["from"] == "0"


def test_the_document_restriction_can_be_switched_off() -> None:
    endpoint = Endpoint(pages=[fixture("search-page-empty.atom")])
    connector = build(endpoint, rechtspraak_documents_only=False)
    try:
        list(connector.discover(None, None))
    finally:
        connector.close()

    assert "return" not in endpoint.searches[0]


def test_an_upper_bound_alone_still_sends_a_range() -> None:
    endpoint = Endpoint(pages=[fixture("search-page-empty.atom")])
    connector = build(endpoint)
    try:
        list(connector.discover(None, datetime(2026, 7, 1, tzinfo=UTC)))
    finally:
        connector.close()

    bounds = endpoint.searches[0].get_list("modified")
    assert len(bounds) == 2
    assert bounds[0].startswith("1900-01-01")


def test_the_page_size_is_clamped_to_what_the_endpoint_serves() -> None:
    endpoint = Endpoint(pages=[fixture("search-page-empty.atom")])
    connector = build(endpoint, pipeline_page_size=1000)
    try:
        list(connector.discover(None, None))
    finally:
        connector.close()

    assert endpoint.searches[0]["max"] == "1000"


def test_discovery_pages_until_a_short_page_ends_it() -> None:
    endpoint = Endpoint(
        pages=[
            fixture("search-page-1.atom"),
            fixture("search-page-1.atom"),
            fixture("search-page-empty.atom"),
        ]
    )
    connector = build(endpoint, pipeline_page_size=3)
    try:
        found = list(connector.discover(None, None))
    finally:
        connector.close()

    assert len(found) == 6
    assert [query["from"] for query in endpoint.searches] == ["0", "3", "6"]
    assert [candidate.cursor for candidate in found[3:]] == ["3", "4", "5"]


def test_discovery_streams_rather_than_reading_the_whole_window() -> None:
    endpoint = Endpoint(pages=[fixture("search-page-1.atom")] * 5)
    connector = build(endpoint, pipeline_page_size=3)
    try:
        stream = connector.discover(None, None)
        next(stream)

        assert len(endpoint.searches) == 1

        next(stream)
        next(stream)
        next(stream)

        assert len(endpoint.searches) == 2
    finally:
        connector.close()


def test_an_entry_modified_after_the_upper_bound_ends_the_stream() -> None:
    endpoint = Endpoint(pages=[fixture("search-page-1.atom")] * 3)
    connector = build(endpoint, pipeline_page_size=3)
    try:
        found = list(connector.discover(None, datetime(2026, 6, 1, 6, 45, tzinfo=UTC)))
    finally:
        connector.close()

    assert [candidate.source_id for candidate in found] == [PAGE_ONE[0]]


def test_an_entry_modified_before_the_lower_bound_is_skipped() -> None:
    endpoint = Endpoint(pages=[fixture("search-page-1.atom"), fixture("search-page-empty.atom")])
    connector = build(endpoint, pipeline_page_size=3)
    try:
        found = list(connector.discover(datetime(2026, 6, 1, 6, 45, tzinfo=UTC), None))
    finally:
        connector.close()

    assert PAGE_ONE[0] not in [candidate.source_id for candidate in found]
    assert len(found) == 2


def test_a_broken_feed_ends_the_run_rather_than_one_document() -> None:
    endpoint = Endpoint(pages=[b"<feed><entry>"])
    connector = build(endpoint)
    try:
        with pytest.raises(SourceUnavailableError, match="not well-formed"):
            list(connector.discover(None, None))
    finally:
        connector.close()


# -- Fetching -------------------------------------------------------------------------


def test_a_document_that_is_not_published_is_one_skipped_document(
    cbb: RechtspraakConnector,
) -> None:
    with pytest.raises(DocumentUnavailableError):
        cbb.fetch(candidate_for("ECLI:NL:RBDHA:2099:1"))


def test_a_malformed_identifier_never_costs_a_request() -> None:
    endpoint = Endpoint()
    connector = build(endpoint)
    try:
        with pytest.raises(DocumentUnavailableError, match="well-formed ECLI"):
            connector.fetch(candidate_for("../../etc/passwd"))
    finally:
        connector.close()

    assert endpoint.requests == []


def test_the_payload_is_kept_verbatim(cbb: RechtspraakConnector) -> None:
    raw = cbb.fetch(candidate_for("ECLI:NL:CBB:2024:147"))

    assert raw.payload.encode("utf-8") == fixture("ECLI_NL_CBB_2024_147.xml").decode(
        "utf-8-sig"
    ).encode("utf-8")
    assert raw.media_format == "xml"
    assert raw.retrieved_at.tzinfo is not None


# -- Normalisation --------------------------------------------------------------------


def test_a_recorded_judgment_is_mapped_field_by_field(cbb: RechtspraakConnector) -> None:
    case = normalise(cbb, "ECLI:NL:CBB:2024:147")

    assert case.source_id == "ECLI:NL:CBB:2024:147"
    assert case.jurisdiction_code == "NL"
    assert case.source_system == "rechtspraak"
    assert case.title == (
        "ECLI:NL:CBB:2024:147 College van Beroep voor het bedrijfsleven , 05-03-2024 / 21/917"
    )
    assert case.abstract is not None
    assert case.abstract.startswith("Afwijzing verzoek om handhaving niet zorgvuldig voorbereid")
    assert case.subject == "Bestuursrecht"
    assert case.decision_date is not None and case.decision_date.isoformat() == "2024-03-05"
    assert case.publication_date is not None and case.publication_date.isoformat() == "2024-03-01"
    assert case.filing_date is None
    assert case.case_numbers == ("21/917",)
    assert case.language == "nl"
    assert case.law_domain is LawDomain.PUBLIC
    assert case.law_subfield is None
    assert case.procedure_type == "Eerste aanleg - meervoudig"
    assert case.outcome is None
    assert case.source_url == ("https://uitspraken.rechtspraak.nl/details?id=ECLI:NL:CBB:2024:147")
    assert case.is_published is True
    # The connector leaves the hash alone: discovery's Atom ``updated`` is the authority.
    assert case.content_hash is None


def test_the_court_comes_from_the_vocabulary_not_from_a_name(cbb: RechtspraakConnector) -> None:
    case = normalise(cbb, "ECLI:NL:CBB:2024:147")

    assert case.court is not None
    assert case.court.source_identifier == (
        "http://standaarden.overheid.nl/owms/terms/College_van_Beroep_voor_het_bedrijfsleven"
    )
    assert case.court.name == "College van Beroep voor het bedrijfsleven"
    assert case.court.abbreviation == "CBB"
    # Supplied on every case on purpose: persistence writes these onto the court row each
    # time, so leaving them None would erase what seeding put there.
    assert case.court.level == "supreme"
    assert case.court.domain == "administrative"


def test_the_metadata_block_is_kept_whole(cbb: RechtspraakConnector) -> None:
    case = normalise(cbb, "ECLI:NL:CBB:2024:147")
    metadata = case.source_metadata

    assert metadata["zittingsplaatsen"] == ["Den Haag"]
    assert metadata["coverage"] == "NL"
    assert metadata["modified"] == "2024-03-05T13:14:06"
    assert metadata["access_rights"] == "public"
    assert metadata["publisher"]["name"] == "Raad voor de Rechtspraak"
    assert metadata["document_type"]["uri"] == "http://psi.rechtspraak.nl/uitspraak"
    assert metadata["vindplaatsen"] == ["Rechtspraak.nl"]
    assert metadata["deeplink"] == (
        "http://deeplink.rechtspraak.nl/uitspraak?id=ECLI:NL:CBB:2024:147"
    )
    assert metadata["has_body"] is True
    # Every element of the block survives, including the ones with no column of their own and
    # the resource identifiers behind the controlled values.
    block = metadata["dublin_core"]
    assert block["dcterms:spatial"][0]["value"] == "Den Haag"
    assert block["dcterms:creator"][0]["attributes"]["rdfs:label"] == "Instantie"
    assert block["dcterms:subject"][0]["attributes"]["resourceIdentifier"].endswith(
        "#bestuursrecht"
    )
    # A repeated element is several entries, never one overwriting another - in the verbatim
    # block and in the lifted view alike, which is what makes the lifted key safe to read.
    assert len(block["psi:procedure"]) == 2
    assert [entry["label"] for entry in metadata["procedures"]] == [
        "Eerste aanleg - meervoudig",
        "Proceskostenveroordeling",
    ]
    assert metadata["procedures"][1]["uri"].endswith("#proceskostenveroordeling")
    # A key whose element is repeatable is a list even when this document carries one value,
    # so a reader can tell from the key alone whether a second value is possible.
    assert metadata["rechtsgebieden"] == [
        {
            "label": "Bestuursrecht",
            "uri": "http://psi.rechtspraak.nl/rechtsgebied#bestuursrecht",
        }
    ]


def test_the_judgment_text_is_extracted_for_the_filter(cbb: RechtspraakConnector) -> None:
    case = normalise(cbb, "ECLI:NL:CBB:2024:147")

    assert len(case.documents) == 1
    document = case.documents[0]
    assert document.doc_type is DocumentType.JUDGMENT
    assert document.language == "nl"
    assert document.media_format == "xml"
    assert document.raw_payload is not None
    assert document.raw_payload.lstrip("﻿").startswith("<?xml")
    assert case.full_text is not None
    assert "COLLEGE VAN BEROEP VOOR HET BEDRIJFSLEVEN" in case.full_text
    assert "zaaknummer: 21/917" in case.full_text
    # Extraction breaks lines at block boundaries rather than running two headings together
    # into one word, which would put a curated term out of reach of a word-boundary match.
    assert "bedrijfslevenzaaknummer" not in case.full_text.lower().replace(" ", "")
    # The source XML is pretty-printed; none of its indentation survives into the text.
    assert "\n   " not in case.full_text


def test_a_statute_reference_becomes_a_citation(cbb: RechtspraakConnector) -> None:
    case = normalise(cbb, "ECLI:NL:CBB:2024:147")

    assert len(case.citations) == 1
    citation = case.citations[0]
    assert citation.target_scheme == "bwb"
    assert citation.citation_type == "cites"
    assert citation.target_identifier.startswith("jci1.31:c:BWBR0030250")
    assert citation.target_title == "Wet dieren"


def test_a_related_decision_becomes_an_ecli_citation() -> None:
    endpoint = Endpoint(
        documents={"ECLI:NL:HR:2024:309": fixture("ECLI_NL_HR_2024_309.xml")},
        vocabulary=fixture("instanties.xml"),
    )
    connector = build(endpoint)
    try:
        case = normalise(connector, "ECLI:NL:HR:2024:309")
    finally:
        connector.close()

    related = [citation for citation in case.citations if citation.target_scheme == "ecli"]
    assert related
    assert related[0].citation_type == "related"
    assert related[0].target_identifier.startswith("ECLI:NL:")
    assert case.court is not None
    assert case.court.level == "supreme"


def test_an_advocate_generals_opinion_is_stored_as_an_opinion() -> None:
    endpoint = Endpoint(
        documents={"ECLI:NL:PHR:2024:321": fixture("ECLI_NL_PHR_2024_321.xml")},
        vocabulary=fixture("instanties.xml"),
    )
    connector = build(endpoint)
    try:
        case = normalise(connector, "ECLI:NL:PHR:2024:321")
    finally:
        connector.close()

    assert [document.doc_type for document in case.documents] == [DocumentType.OPINION]
    assert case.full_text
    assert case.law_domain is LawDomain.CRIMINAL


def test_a_metadata_only_ecli_is_stored_without_being_scored() -> None:
    endpoint = Endpoint(
        documents={"ECLI:NL:GHAMS:2026:1495": fixture("ECLI_NL_GHAMS_2026_1495.xml")},
        vocabulary=fixture("instanties.xml"),
    )
    connector = build(endpoint)
    try:
        case = normalise(connector, "ECLI:NL:GHAMS:2026:1495")
    finally:
        connector.close()

    assert case.abstract is None
    # The filter is handed nothing at all rather than an empty string to score.
    assert case.full_text is None
    assert len(case.documents) == 1
    assert case.documents[0].doc_type is DocumentType.OTHER
    assert case.documents[0].full_text is None
    # The payload is still kept, so a later reclassification needs no re-fetch.
    assert case.documents[0].raw_payload
    assert case.source_metadata["has_body"] is False
    assert case.court is not None

    verdict = KeywordFilter.for_jurisdiction("NL", settings=settings_for()).evaluate(case)
    assert verdict.passed is False


def test_a_document_that_is_not_xml_is_one_failed_document(cbb: RechtspraakConnector) -> None:
    raw = cbb.fetch(candidate_for("ECLI:NL:CBB:2024:147"))
    broken = RawDocument(
        candidate=raw.candidate,
        payload="<open-rechtspraak><rdf:RDF>",
        media_format="xml",
    )

    with pytest.raises(DocumentUnavailableError, match="not well-formed"):
        cbb.normalise(broken)


# -- Multi-valued fields --------------------------------------------------------------


@pytest.fixture
def multivalued() -> Iterator[RechtspraakConnector]:
    """Return a connector serving the hand-built document that repeats every field."""
    endpoint = Endpoint(
        documents={"ECLI:NL:RVS:2024:9001": fixture("multivalued.xml")},
        vocabulary=fixture("instanties.xml"),
    )
    connector = build(endpoint)
    try:
        yield connector
    finally:
        connector.close()


def test_repeated_fields_stay_lists(multivalued: RechtspraakConnector) -> None:
    case = normalise(multivalued, "ECLI:NL:RVS:2024:9001")

    assert case.case_numbers == ("202301234/1/R4", "202301235/1/R4", "202301236/1/R4")
    assert case.source_metadata["zaaknummers"] == list(case.case_numbers)
    assert [entry["label"] for entry in case.source_metadata["rechtsgebieden"]] == [
        "Bestuursrecht; Omgevingsrecht",
        "Bestuursrecht; Europees bestuursrecht",
    ]
    assert [entry["label"] for entry in case.source_metadata["procedures"]] == [
        "Eerste en enige aanleg",
        "Proceskostenveroordeling",
    ]
    # The column holds one procedure because the schema has one column; the list keeps both.
    assert case.procedure_type == "Eerste en enige aanleg"
    assert case.source_metadata["vindplaatsen"] == [
        "Rechtspraak.nl",
        "AB 2024/211 met annotatie van A. Voorbeeld",
        "JM 2024/98",
    ]


def test_every_rechtsgebied_reaches_the_scored_subject(multivalued: RechtspraakConnector) -> None:
    case = normalise(multivalued, "ECLI:NL:RVS:2024:9001")

    assert case.subject is not None
    assert "Omgevingsrecht" in case.subject
    assert "Europees bestuursrecht" in case.subject
    # The hierarchy gives the domain and the subfield without a second lookup.
    assert case.law_domain is LawDomain.PUBLIC
    assert case.law_subfield == "Omgevingsrecht"


def test_references_of_several_schemes_all_become_citations(
    multivalued: RechtspraakConnector,
) -> None:
    case = normalise(multivalued, "ECLI:NL:RVS:2024:9001")

    by_scheme = {citation.target_scheme: citation for citation in case.citations}
    assert set(by_scheme) == {"bwb", "celex", "ecli"}
    assert by_scheme["celex"].target_identifier == "32009R1107"
    assert by_scheme["ecli"].target_identifier == "ECLI:NL:RBOBR:2023:4455"
    assert by_scheme["ecli"].source_metadata["psi:aanleg"].endswith("eerdereAanleg")


def test_a_pesticide_judgment_passes_the_shipped_dutch_list(
    multivalued: RechtspraakConnector,
) -> None:
    case = normalise(multivalued, "ECLI:NL:RVS:2024:9001")

    verdict = KeywordFilter.for_jurisdiction("NL", settings=settings_for()).evaluate(case)

    assert verdict.passed is True
    assert {match.term_id for match in verdict.matches} >= {"nl-gewasbeschermingsmiddel"}


def test_a_line_break_instruction_keeps_the_text_that_follows_it(
    multivalued: RechtspraakConnector,
) -> None:
    case = normalise(multivalued, "ECLI:NL:RVS:2024:9001")

    assert case.full_text is not None
    assert "uitspraak van de meervoudige kamer" in case.full_text


# -- Hardening ------------------------------------------------------------------------


def test_an_external_entity_is_never_dereferenced() -> None:
    endpoint = Endpoint(
        documents={"ECLI:NL:RVS:2024:9002": fixture("external-entity.xml")},
        vocabulary=fixture("instanties.xml"),
    )
    connector = build(endpoint)
    try:
        case = normalise(connector, "ECLI:NL:RVS:2024:9002")
    finally:
        connector.close()

    assert case.full_text is not None
    # The reference survives as literal text: nothing was read off disk and nothing was
    # fetched from the attacker's host.
    assert "&secret;" in case.full_text
    assert "root:" not in case.full_text
    assert not any("attacker.invalid" in str(request.url) for request in endpoint.requests)


def test_an_entity_expansion_bomb_is_refused() -> None:
    endpoint = Endpoint(
        documents={"ECLI:NL:RVS:2024:9003": fixture("entity-expansion.xml")},
        vocabulary=fixture("instanties.xml"),
    )
    connector = build(endpoint)
    try:
        with pytest.raises(DocumentUnavailableError):
            normalise(connector, "ECLI:NL:RVS:2024:9003")
    finally:
        connector.close()


def test_every_request_identifies_the_project() -> None:
    endpoint = Endpoint(pages=[fixture("search-page-empty.atom")])
    connector = build(endpoint)
    try:
        list(connector.discover(None, None))
    finally:
        connector.close()

    agent = endpoint.requests[0].headers["User-Agent"]
    assert "PLT/" in agent
    assert "github.com/IdseVal/PLT" in agent


# -- The controlled vocabulary --------------------------------------------------------


def test_the_court_vocabulary_is_read_once_per_connector() -> None:
    endpoint = Endpoint(
        documents={
            "ECLI:NL:CBB:2024:147": fixture("ECLI_NL_CBB_2024_147.xml"),
            "ECLI:NL:HR:2024:309": fixture("ECLI_NL_HR_2024_309.xml"),
        },
        vocabulary=fixture("instanties.xml"),
    )
    connector = build(endpoint)
    try:
        normalise(connector, "ECLI:NL:CBB:2024:147")
        normalise(connector, "ECLI:NL:HR:2024:309")
    finally:
        connector.close()

    vocabulary_requests = [
        request for request in endpoint.requests if str(request.url).endswith("/Instanties")
    ]
    assert len(vocabulary_requests) == 1


def test_a_missing_vocabulary_does_not_stop_a_run() -> None:
    endpoint = Endpoint(
        documents={"ECLI:NL:CBB:2024:147": fixture("ECLI_NL_CBB_2024_147.xml")},
        vocabulary=None,
    )
    connector = build(endpoint, http_max_retries=0)
    try:
        case = normalise(connector, "ECLI:NL:CBB:2024:147")
    finally:
        connector.close()

    assert case.court is not None
    assert case.court.name == "College van Beroep voor het bedrijfsleven"
    assert case.court.level is None


def test_seeding_refuses_to_pretend_the_vocabulary_was_empty() -> None:
    connector = build(Endpoint(vocabulary=None), http_max_retries=0)
    try:
        with pytest.raises(SourceUnavailableError, match="court vocabulary"):
            list(connector.iter_courts())
    finally:
        connector.close()


def test_the_vocabulary_supplies_a_level_for_every_kind_of_court() -> None:
    connector = build(Endpoint(vocabulary=fixture("instanties.xml")))
    try:
        courts = list(connector.iter_courts())
    finally:
        connector.close()

    assert len(courts) == 14
    assert all(court.source_identifier.startswith("http") for court in courts)
    levels = {court.abbreviation: court.level for court in courts}
    assert levels["RBDHA"] == "first_instance"
    assert levels["GHAMS"] == "appeal"
    assert levels["HR"] == "supreme"
    assert levels["RVS"] == "supreme"
    assert levels["PHR"] == "advisory"
    domains = {court.abbreviation: court.domain for court in courts}
    assert domains["RVS"] == "administrative"
    assert domains["CBB"] == "administrative"


@pytest.mark.parametrize(
    ("label", "domain", "subfield"),
    [
        ("Bestuursrecht; Vreemdelingenrecht", LawDomain.PUBLIC, "Vreemdelingenrecht"),
        ("Civiel recht; Verbintenissenrecht", LawDomain.PRIVATE, "Verbintenissenrecht"),
        ("Strafrecht", LawDomain.CRIMINAL, None),
        ("Internationaal publiekrecht; Mensenrechten", LawDomain.PUBLIC, "Mensenrechten"),
        ("Iets heel anders", LawDomain.OTHER, None),
    ],
)
def test_the_rechtsgebied_hierarchy_gives_both_classification_columns(
    label: str,
    domain: LawDomain,
    subfield: str | None,
) -> None:
    assert RechtspraakConnector._classify([label]) == (domain, subfield)


def test_a_document_with_no_rechtsgebied_is_left_unclassified() -> None:
    assert RechtspraakConnector._classify([]) == (None, None)


# -- Lifecycle ------------------------------------------------------------------------


def test_a_connector_that_built_its_own_client_closes_it() -> None:
    connector = RechtspraakConnector(settings_for())
    connector.close()

    with pytest.raises(RuntimeError):
        connector.fetch(candidate_for("ECLI:NL:CBB:2024:147"))


def test_a_connector_leaves_an_injected_client_alone() -> None:
    endpoint = Endpoint(documents={"ECLI:NL:CBB:2024:147": fixture("ECLI_NL_CBB_2024_147.xml")})
    settings = settings_for()
    client = PoliteClient(settings, transport=httpx.MockTransport(endpoint), sleep=lambda _: None)
    connector = RechtspraakConnector(settings, client=client)

    connector.close()
    try:
        assert client.get(CONTENT_URL, params={"id": "ECLI:NL:CBB:2024:147"}).status_code == 200
    finally:
        client.close()


def test_the_connector_is_registered_for_the_netherlands() -> None:
    from plt.pipeline import registry

    registry.reset_registry()
    try:
        assert registry.connector_classes()["NL"] is RechtspraakConnector
        assert RechtspraakConnector.name == "rechtspraak"
    finally:
        registry.reset_registry()


def test_a_source_without_a_vocabulary_seeds_nothing() -> None:
    # The base class's default: a source that publishes no vocabulary seeds nothing rather
    # than making every other connector implement an empty method.
    assert list(FakeConnector(build_settings()).iter_courts()) == []
