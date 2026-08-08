"""European Union connector: EUR-Lex through the CELLAR repository.

The EU is a jurisdiction in its own right in this database, never an aggregate of its member
states (``docs/core-document.md`` section 3.3), so this connector produces ``EU`` cases on
exactly the same footing as the Dutch one produces ``NL`` cases.

Two endpoints, both recorded in Annex 2a of the core document:

* ``settings.eurlex_sparql_url`` — the CELLAR SPARQL 1.1 endpoint over the CDM ontology,
  used by :meth:`EurLexConnector.discover` to enumerate CELEX sector 6 (case law).
* ``settings.eurlex_cellar_base_url`` — the CELLAR REST route. ``Accept:
  application/xml;notice=object`` returns the metadata notice for a CELEX;
  ``Accept: application/xhtml+xml`` with an ``Accept-Language`` returns one language
  manifestation of the full text.

Five properties of this source shape the implementation.

**The 10,000-result cap.** Since 1 January 2026 a single CELLAR search returns at most
10,000 results, so discovery may not issue one unbounded query. It walks date windows of
``eurlex_window_days``, counts each window before paging it, and halves any window that
reaches ``eurlex_max_results`` until it fits — down to ``eurlex_min_window_seconds``, past
which it processes the window anyway and says so in the log rather than splitting forever.

**Paging has to be by a key, not by an offset.** A window is paged on the CELEX number
itself, each page asking for the numbers above the last one the previous page returned.
``LIMIT``/``OFFSET`` over ``ORDER BY`` an aggregate alias looks equivalent and is not: see
:meth:`EurLexConnector._page_query`, which is where the reason and the measurement are.

**One case per CELEX.** A CELEX number resolves to several cellar works: the complex work,
its members, and rectified versions carrying ``do_not_index``. The discovery query therefore
groups by CELEX and aggregates over the works, so a case is discovered once however many
works stand behind it. Language versions are not cases either: they become ``case_document``
rows on the one case (``docs/architecture.md`` section 3).

**Which date bounds the window.** ``eurlex_discovery_date`` chooses. In the default
``modification`` mode the window bounds the repository's own ``lastModificationDate``, which
is what makes a weekly run pick up newly published *and* newly corrected decisions, and what
the ``ingest_checkpoint`` timestamp means. In ``document`` mode it bounds the date of the
decision itself, for backfilling a historical period; candidates then carry no
``modified_at``, so a backfill cannot push the incremental checkpoint forward over work it
never looked at.

**Multilingual matching.** A judgment exists in up to 24 languages.
``NormalisedCase.full_text`` joins every retrieved language version, so a curated term in any
of them qualifies the case — which is precisely why ``data/keywords/eu.json`` carries
English, French, German and Dutch sections. Which languages are retrieved is configuration
(``eurlex_languages``, English by default), with the procedural language as the fallback when
none of the preferred ones exists, and the choice is recorded on the document row.

**Everything is kept.** The notice XML is stored verbatim as its own document's
``raw_payload``, each manifestation as its own, and every CDM field without a column of its
own goes into ``source_metadata``, so a later reclassification never has to go back to the
Publications Office (``docs/architecture.md`` rule 2.6).

Two things this connector deliberately does *not* do. It does not guess ``law_domain`` or
``law_subfield``: the CDM classification is a policy tree ("Internal policy of the European
Union → Chemicals → Plant protection products"), not the public/private/criminal distinction
the schema asks for, and a wrong classification is worse than a missing one in a research
database. And it does not compose its own HTTP — every request goes through
:class:`plt.pipeline.http.PoliteClient`, which owns the request rate, the backoff and the
``Retry-After`` handling a public research endpoint is entitled to.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final
from urllib.parse import quote

from lxml import etree

from plt.config import EurLexDiscoveryDate, Settings
from plt.db.models import DocumentType, PartyRole
from plt.pipeline.base import (
    Candidate,
    DocumentUnavailableError,
    NormalisedCase,
    NormalisedCitation,
    NormalisedCourt,
    NormalisedDocument,
    NormalisedParty,
    RawDocument,
    SourceConnector,
    SourceTraffic,
    SourceUnavailableError,
)
from plt.pipeline.http import PoliteClient
from plt.utils.logging import get_logger

__all__ = ["EurLexConnector"]

log = get_logger(__name__)

# --------------------------------------------------------------------------------------
# CELLAR vocabulary. These are facts about the source rather than configuration: the CDM
# property names, the authority-list URIs and the case-law sector number cannot vary by
# deployment, and a run against a CELLAR where they had changed would be broken, not
# differently configured.
# --------------------------------------------------------------------------------------

#: SPARQL prefixes shared by every query this connector sends.
_PREFIXES: Final[str] = (
    "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
    "PREFIX cmr: <http://publications.europa.eu/ontology/cdm/cmr#>\n"
    "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
)

#: CELEX sector holding case law.
_CASE_LAW_SECTOR: Final[str] = "6"

#: Base of the controlled-vocabulary URIs the queries bind against.
_AUTHORITY: Final[str] = "http://publications.europa.eu/resource/authority"

#: Where a run with no lower bound starts. The Court of Justice of the ECSC was established
#: in 1952, so nothing in sector 6 predates it.
_EPOCH: Final[datetime] = datetime(1952, 1, 1, tzinfo=UTC)

#: Notice flavour fetched per case: the full metadata object notice of one work.
_NOTICE_ACCEPT: Final[str] = "application/xml;notice=object"

#: Accept for a full-text manifestation. CELLAR answers ``text/html`` alone with a 404 for
#: most judgments and serves the same document as XHTML, so both are offered (verified
#: 4 August 2026).
_TEXT_ACCEPT: Final[str] = "application/xhtml+xml, text/html"

#: ISO 639-3 to ISO 639-1 for the 24 official languages. ``case.language`` is 639-1 by
#: contract (``docs/architecture.md`` section 3); an unlisted code is kept lower-cased, so a
#: new official language degrades to a slightly odd value rather than to ``None``.
_ISO_639_1: Final[Mapping[str, str]] = {
    "BUL": "bg",
    "CES": "cs",
    "DAN": "da",
    "DEU": "de",
    "ELL": "el",
    "ENG": "en",
    "EST": "et",
    "FIN": "fi",
    "FRA": "fr",
    "GLE": "ga",
    "HRV": "hr",
    "HUN": "hu",
    "ITA": "it",
    "LAV": "lv",
    "LIT": "lt",
    "MLT": "mt",
    "NLD": "nl",
    "POL": "pl",
    "POR": "pt",
    "RON": "ro",
    "SLK": "sk",
    "SLV": "sl",
    "SPA": "es",
    "SWE": "sv",
}

#: CDM resource type to the schema's document kind. ``ORDER`` maps to ``other`` on purpose:
#: an order is not a judgment, the enum has no member for it, and filing it under
#: ``judgment`` would make the distinction unrecoverable. The exact CDM type is kept on the
#: document's ``source_metadata`` either way.
_DOCUMENT_TYPES: Final[Mapping[str, DocumentType]] = {
    "JUDG": DocumentType.JUDGMENT,
    "JUDG_EXTRACT": DocumentType.JUDGMENT,
    "OPIN_AG": DocumentType.OPINION,
    "OPIN_JUR": DocumentType.OPINION,
    "SUM_JUR": DocumentType.SUMMARY,
    "ABSTRACT_JUR": DocumentType.SUMMARY,
    "ORDER": DocumentType.OTHER,
}

#: Notice elements naming another instrument or decision, and the relation each records.
#: This is what makes an EU pesticide-regulation citation graph possible later, so the
#: relation CELLAR stated is preserved rather than flattened to "cites".
_CITATION_RELATIONS: Final[Mapping[str, str]] = {
    "WORK_CITES_WORK": "cites",
    "CASE-LAW_INTERPRETES_RESOURCE_LEGAL": "interprets",
    "CASE-LAW_DECLARES_VALID_RESOURCE_LEGAL": "declares_valid",
    "CASE-LAW_DECLARES_VOID_RESOURCE_LEGAL": "declares_void",
    "CASE-LAW_APPLIES_RESOURCE_LEGAL": "applies",
    "CASE-LAW_BASED_ON_RESOURCE_LEGAL": "based_on",
    "RESOURCE_LEGAL_INCORPORATES_RESOURCE_LEGAL": "incorporates",
}

#: Notice elements holding the subject-matter and descriptor labels, joined into
#: ``NormalisedCase.subject`` — a scored field of the keyword filter.
_SUBJECT_ELEMENTS: Final[tuple[str, ...]] = (
    "RESOURCE_LEGAL_IS_ABOUT_SUBJECT-MATTER",
    "CASE-LAW_IS-ABOUT_CASE-LAW-SUBJECT-MATTER",
    "CASE-LAW_IS_ABOUT_CONCEPT",
    "CASE-LAW_IS_ABOUT_CONCEPT_NEW_CASE-LAW",
    "IS_ABOUT",
)

#: A CELEX number as it may appear in a URL path or a SPARQL literal. Anchored, and with no
#: slash, quote, angle bracket or whitespace in the character set, so a value that matches
#: can express neither a path traversal nor a SPARQL injection.
_CELEX: Final[re.Pattern[str]] = re.compile(r"[0-9][0-9A-Za-z()._-]{3,39}")

#: Shape of a controlled-vocabulary code that may be interpolated into a SPARQL URI.
_VOCABULARY_CODE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_]{1,32}")

#: CELLAR separates the segments of a case title — court and date, parties, keywords, case
#: number — with this character.
_TITLE_SEPARATOR: Final[str] = "#"

#: How a CJEU title names the two sides of a direct action.
_PARTY_SEPARATOR: Final[re.Pattern[str]] = re.compile(r"\s+v\.?\s+")

#: Collapses the whitespace a converted judgment is full of.
_WHITESPACE: Final[re.Pattern[str]] = re.compile(r"\s+")

#: Elements whose text a reader never sees, skipped when extracting a manifestation.
_INVISIBLE_TAGS: Final[frozenset[str]] = frozenset({"script", "style"})


def _safe_parser() -> etree.XMLParser:
    """Build an XML parser that cannot be talked into fetching or expanding anything.

    Both payloads this connector parses are untrusted: the CELLAR notice is XML, and a
    manifestation is XHTML carrying a ``<!DOCTYPE ... "xhtml-strict.dtd">`` reference.
    Entity resolution, DTD loading and network access are therefore all off, which closes
    XXE and billion-laughs entity expansion, and ``huge_tree`` stays off so that a
    pathological document cannot exhaust memory.

    Returns:
        A parser configured for untrusted input. A new one per call, because :mod:`lxml`
        parsers hold state and are not safe to share.
    """
    return etree.XMLParser(
        resolve_entities=False,
        load_dtd=False,
        dtd_validation=False,
        no_network=True,
        huge_tree=False,
    )


def _parse(payload: bytes, *, what: str, source_id: str) -> etree._Element:
    """Parse a source payload with the hardened parser.

    Args:
        payload: The response body, as bytes so that the declared encoding is honoured.
        what: What is being parsed, for the error message.
        source_id: CELEX number of the document, for the error message.

    Returns:
        The document root.

    Raises:
        DocumentUnavailableError: If the payload is not well-formed XML. Scoped to this one
            document, so the run carries on with the next candidate.
    """
    try:
        return etree.fromstring(payload, parser=_safe_parser())
    except etree.XMLSyntaxError as error:
        message = f"{source_id}: the {what} is not well-formed XML: {error}"
        raise DocumentUnavailableError(message) from error


def _validate_celex(value: str) -> str:
    """Check a CELEX number before it reaches a URL path or a SPARQL literal.

    Args:
        value: The identifier, from a source payload or from a caller.

    Returns:
        The identifier, stripped.

    Raises:
        DocumentUnavailableError: If it is not a CELEX number.
    """
    celex = value.strip()
    if not _CELEX.fullmatch(celex):
        message = f"{value!r} is not a CELEX number"
        raise DocumentUnavailableError(message)
    return celex


def _instant_literal(moment: datetime) -> str:
    """Render an instant as an ``xsd:dateTime`` literal.

    Args:
        moment: The instant. A naive value is read as UTC, which is what the pipeline uses
            throughout.

    Returns:
        A typed SPARQL literal, formatted from the components of a :class:`datetime`, so no
        caller-supplied text reaches the query.
    """
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return f'"{aware.astimezone(UTC):%Y-%m-%dT%H:%M:%S}Z"^^xsd:dateTime'


def _date_literal(moment: datetime) -> str:
    """Render the date part of an instant as an ``xsd:date`` literal.

    Args:
        moment: The instant.

    Returns:
        A typed SPARQL literal, formatted from the components of a :class:`datetime`.
    """
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return f'"{aware.astimezone(UTC):%Y-%m-%d}"^^xsd:date'


def _celex_literal(celex: str) -> str:
    """Check a CELEX number immediately before it becomes part of a SPARQL query.

    The second check, as :func:`_authority_uri` is for a vocabulary code: the caller has
    already refused anything that is not a CELEX number, and this is what makes that
    impossible to bypass by a later edit. A CELEX number can carry no quote, backslash,
    angle bracket or whitespace, so one that passes here cannot close the literal it is
    written into.

    Args:
        celex: The identifier, already validated by :func:`_paging_key`.

    Returns:
        The identifier, unchanged.

    Raises:
        ValueError: If it is not a CELEX number, which would mean the caller's check had
            been bypassed.
    """
    if not _CELEX.fullmatch(celex):
        message = f"{celex!r} is not a CELEX number and must not reach a query"
        raise ValueError(message)
    return celex


def _authority_uri(collection: str, code: str) -> str:
    """Render a controlled-vocabulary URI for a SPARQL query.

    Args:
        collection: Authority list, e.g. ``language`` or ``resource-type``.
        code: Code within that list. :class:`~plt.config.Settings` already refuses anything
            but letters, digits and underscores; this is the second check, immediately
            before the value becomes part of a query.

    Returns:
        The URI in angle brackets.

    Raises:
        ValueError: If the code is not one, which would mean the configuration validator had
            been bypassed.
    """
    if not _VOCABULARY_CODE.fullmatch(code):
        message = f"{code!r} is not a controlled-vocabulary code"
        raise ValueError(message)
    return f"<{_AUTHORITY}/{collection}/{code}>"


def _text_of(element: etree._Element | None) -> str | None:
    """Return an element's stripped text.

    Args:
        element: The element, or ``None``.

    Returns:
        The text, or ``None`` when the element is absent or blank.
    """
    if element is None or element.text is None:
        return None
    return element.text.strip() or None


def _find_text(parent: etree._Element | None, path: str) -> str | None:
    """Return the text at a path below an element.

    Args:
        parent: Element to search below, or ``None``.
        path: ElementPath expression.

    Returns:
        The stripped text, or ``None``.
    """
    if parent is None:
        return None
    return _text_of(parent.find(path))


def _parse_date(value: str | None) -> date | None:
    """Parse a CELLAR ``YYYY-MM-DD`` date.

    Args:
        value: The value, or ``None``.

    Returns:
        The date, or ``None`` when it is absent or unparseable. A malformed date is not worth
        losing a case over, and the notice that carried it is kept verbatim regardless.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_instant(value: str | None) -> datetime | None:
    """Parse a CELLAR timestamp into a timezone-aware instant.

    Args:
        value: An ISO 8601 timestamp, with or without an offset, or ``None``.

    Returns:
        The instant in UTC, or ``None`` when it is absent or unparseable.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    aware = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return aware.astimezone(UTC)


def _collapse(text: str) -> str:
    """Collapse runs of whitespace into single spaces.

    Args:
        text: The text to normalise.

    Returns:
        The text with its whitespace collapsed and its ends stripped.
    """
    return _WHITESPACE.sub(" ", text).strip()


@dataclass(frozen=True, slots=True)
class _Window:
    """One date window of a discovery walk.

    Attributes:
        start: Inclusive lower bound.
        stop: Exclusive upper bound.
    """

    start: datetime
    stop: datetime

    @property
    def width(self) -> timedelta:
        """Return how long the window is."""
        return self.stop - self.start

    def halved(self, floor: timedelta) -> _Window:
        """Return the window halved, never narrower than a floor.

        Args:
            floor: Narrowest the window may become.

        Returns:
            A window starting where this one starts and ending halfway along it, or at the
            floor, whichever is later.
        """
        half = max(self.width / 2, floor)
        return _Window(self.start, min(self.start + half, self.stop))

    def cursor(self, offset: int) -> str:
        """Return the opaque cursor recorded on the checkpoint for a position in the window.

        Args:
            offset: Result offset within the window.

        Returns:
            A short, readable position, well inside the 255 characters
            ``ingest_checkpoint.last_cursor`` holds.
        """
        return f"{self.start:%Y-%m-%dT%H:%M:%SZ}/{self.stop:%Y-%m-%dT%H:%M:%SZ}#{offset}"


def _paging_key(row: Mapping[str, Mapping[str, str]], window: _Window) -> str:
    """Return the CELEX number the next page of a window starts above.

    Args:
        row: The last binding set of the page just read.
        window: The window being enumerated, for the error message.

    Returns:
        The row's CELEX number.

    Raises:
        SourceUnavailableError: If the row carries nothing that can be paged past. A single
            unusable row mid-window is skipped by :meth:`EurLexConnector._candidate`, but one
            in the *last* position of a full page leaves the rest of the window unreachable,
            and a window enumerated to an unknown depth must never be mistaken for a
            complete one.
    """
    value = (row.get("celex", {}).get("value") or "").strip()
    if not _CELEX.fullmatch(value):
        message = (
            f"the window {window.cursor(0)} ended a page on {value[:64]!r}, which is not a "
            f"CELEX number; the rest of the window cannot be paged"
        )
        raise SourceUnavailableError(message)
    return value


def _discovery_order(row: Mapping[str, Mapping[str, str]]) -> tuple[datetime, str]:
    """Return the sort key that puts a window's rows in the order the checkpoint expects.

    Args:
        row: One binding set of a discovery page.

    Returns:
        The row's modification instant and CELEX number. A row CELLAR gave no modification
        date sorts first, which keeps it ahead of the position any later row could leave on
        the checkpoint rather than stranded behind it.
    """
    binding = row.get("modified_at", {}).get("value")
    return _parse_instant(binding) or _EPOCH, row.get("celex", {}).get("value") or ""


class EurLexConnector(SourceConnector):
    """The European Union's case law, from EUR-Lex through CELLAR.

    Attributes:
        jurisdiction_code: ``EU`` — a jurisdiction in its own right.
        name: ``eurlex``; written to ``ingest_run.connector`` and the primary key of this
            connector's ``ingest_checkpoint`` row.
    """

    jurisdiction_code = "EU"
    name = "eurlex"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: PoliteClient | None = None,
    ) -> None:
        """Bind the connector to its configuration.

        Args:
            settings: Settings supplying the endpoints, the window sizes, the languages and
                the politeness budget. Defaults to the process-wide settings, which is how
                :mod:`plt.pipeline.registry` builds it.
            client: HTTP client to send through. Tests pass one over a mock transport;
                production leaves it unset and gets a
                :class:`~plt.pipeline.http.PoliteClient` built from the same settings.
                Ownership passes to the connector either way, so :meth:`close` closes it.
        """
        super().__init__(settings)
        self._client = client if client is not None else PoliteClient(self.settings)

    @property
    def traffic(self) -> SourceTraffic:
        """Return what this connector has asked of CELLAR so far."""
        return self._client.traffic

    # -- discovery ---------------------------------------------------------------------

    def discover(self, since: datetime | None, until: datetime | None) -> Iterator[Candidate]:
        """Yield one candidate per CELEX number in the window, oldest first.

        The window is walked in slices small enough that no single search reaches CELLAR's
        result cap, and each slice is paged through on the CELEX number. Peak memory is one
        slice's candidates, whatever the size of the whole window: a slice is halved until it
        holds fewer than ``eurlex_max_results`` cases, so that setting bounds this as well as
        the query. The one slice it does not bound is a slice already at
        ``eurlex_min_window_seconds`` that still reaches the cap, which :meth:`_windows`
        processes as it stands and warns about — the warning is now about memory as well as
        about reachability.

        Args:
            since: Inclusive lower bound. ``None`` means no lower bound, which walks the
                whole repository; the runner passes the stored checkpoint when the caller
                gave none.
            until: Exclusive upper bound, or ``None`` for "up to now".

        Yields:
            One :class:`~plt.pipeline.base.Candidate` per CELEX number.

        Raises:
            SourceUnavailableError: If CELLAR cannot be enumerated at all.
        """
        start = since if since is not None else _EPOCH
        stop = until if until is not None else datetime.now(UTC)
        if since is None:
            log.warning(
                "no lower bound given; walking the whole of CELLAR case law",
                extra={"context": {"connector": self.name, "from": start.isoformat()}},
            )
        if start >= stop:
            log.info(
                "empty discovery window",
                extra={"context": {"since": start.isoformat(), "until": stop.isoformat()}},
            )
            return
        log.info(
            "discovering EU case law",
            extra={
                "context": {
                    "since": start.isoformat(),
                    "until": stop.isoformat(),
                    "bounded_by": self.settings.eurlex_discovery_date.value,
                    "window_days": self.settings.eurlex_window_days,
                }
            },
        )
        for window, count in self._windows(start, stop):
            # A backfill from 1952 walks hundreds of windows CELLAR holds nothing in; the
            # count already said so, so none of them costs a page query as well.
            if count:
                yield from self._candidates(window)

    def _windows(self, start: datetime, stop: datetime) -> Iterator[tuple[_Window, int]]:
        """Yield date windows each holding fewer results than CELLAR will return.

        Every window is counted before it is paged. One that reaches the cap is halved and
        counted again until it fits, or until it reaches ``eurlex_min_window_seconds`` — at
        which point it is processed as it stands and logged, because splitting an hour into
        minutes will not help a source that genuinely holds that many results in one hour,
        and silently dropping the window would be worse.

        What ``eurlex_max_results`` costs has moved since pages were taken by key rather than
        by offset. It used to be reachability: a page at offset 10,000 was past what one
        search would return, so a window over the cap had a tail nothing could reach. A
        keyset page asks for ``pipeline_page_size`` rows however deep into the window it is,
        so an oversized window is now enumerated in full and the cap bounds what
        :meth:`_candidates` holds in memory instead.

        Args:
            start: Inclusive lower bound of the walk.
            stop: Exclusive upper bound.

        Yields:
            Each window and the number of cases in it, oldest first, covering the range
            without gaps or overlap.
        """
        cap = self.settings.eurlex_max_results
        floor = timedelta(seconds=self.settings.eurlex_min_window_seconds)
        width = timedelta(days=self.settings.eurlex_window_days)
        cursor = start
        while cursor < stop:
            window = _Window(cursor, min(cursor + width, stop))
            count = self._count(window)
            while count >= cap and window.width > floor:
                window = window.halved(floor)
                count = self._count(window)
            if count >= cap:
                log.warning(
                    "a window at the narrowest configured width still reaches the result cap; "
                    "enumerating it whole, which holds more of it in memory at once than "
                    "PLT_EURLEX_MAX_RESULTS asks for. Lower PLT_EURLEX_MIN_WINDOW_SECONDS to "
                    "let it be split further",
                    extra={"context": {"window": window.cursor(0), "cases": count, "cap": cap}},
                )
            elif count:
                log.info(
                    "discovery window",
                    extra={"context": {"window": window.cursor(0), "cases": count}},
                )
            yield window, count
            cursor = window.stop

    def _candidates(self, window: _Window) -> Iterator[Candidate]:
        """Page through one window and yield its candidates.

        The pages arrive in CELEX order, because that is the order that can be paged through
        without losing rows. The checkpoint needs modification order, though — it holds the
        highest instant processed, so a run killed part-way through a window must not leave
        behind a position that the cases still to come in that window sit *below*. The
        window is therefore ordered here, once it has been read, before any of it is handed
        to the runner. That costs one window in memory, which ``eurlex_max_results`` bounds,
        and it is what keeps "oldest first" a promise the caller can resume against.

        Args:
            window: The window to enumerate.

        Yields:
            One candidate per CELEX number in the window, oldest first.
        """
        rows = sorted(self._rows(window), key=_discovery_order)
        for offset, row in enumerate(rows):
            candidate = self._candidate(row, window, offset)
            if candidate is not None:
                yield candidate

    def _rows(self, window: _Window) -> list[Mapping[str, Mapping[str, str]]]:
        """Read every result row of one window, paging by CELEX number.

        Args:
            window: The window to enumerate.

        Returns:
            The rows, in the order CELLAR returned them.

        Raises:
            SourceUnavailableError: If a page ends on a row this connector cannot page past.
                A window that cannot be walked to its end is not a window that happens to be
                short, and treating it as one is the whole reason this method exists.
        """
        page_size = self.settings.pipeline_page_size
        collected: list[Mapping[str, Mapping[str, str]]] = []
        after: str | None = None
        while True:
            rows = self._query(self._page_query(window, page_size, after))
            collected.extend(rows)
            if len(rows) < page_size:
                return collected
            after = _paging_key(rows[-1], window)

    def _candidate(
        self,
        row: Mapping[str, Mapping[str, str]],
        window: _Window,
        offset: int,
    ) -> Candidate | None:
        """Turn one SPARQL result row into a candidate.

        Args:
            row: The binding set, in SPARQL JSON results form.
            window: The window it was found in, for the cursor.
            offset: The page offset it was found at, for the cursor.

        Returns:
            The candidate, or ``None`` when the row carries no usable CELEX number. A
            malformed row is logged and skipped rather than allowed to end the run.
        """

        def value(name: str) -> str | None:
            binding = row.get(name)
            return binding.get("value") if binding else None

        raw_celex = (value("celex") or "").strip()
        if not _CELEX.fullmatch(raw_celex):
            log.warning(
                "skipping a discovery row without a usable CELEX number",
                extra={"context": {"celex": raw_celex[:64]}},
            )
            return None
        by_document_date = self.settings.eurlex_discovery_date is EurLexDiscoveryDate.DOCUMENT
        modified = value("modified_at")
        expression_title = value("title") or value("fallback_title")
        return Candidate(
            source_id=raw_celex,
            jurisdiction_code=self.jurisdiction_code,
            # A backfill walked by document date says nothing about what has changed since,
            # so it must never push the incremental checkpoint forward.
            modified_at=None if by_document_date else _parse_instant(modified),
            # CELLAR's own revision marker, so an unchanged case is skipped without being
            # fetched at all (``docs/architecture.md`` section 4.2).
            content_hash=modified,
            cursor=window.cursor(offset),
            title=_clean_title(expression_title),
            decision_date=_parse_date(value("document_date")),
            source_url=self._celex_url(raw_celex),
            source_metadata={
                "cellar_last_modification_date": modified,
                "discovery_window": window.cursor(offset),
                # The title with CELLAR's segment separators still in it. Normalisation
                # reads the parties out of those segments, and the cleaned title above has
                # lost them.
                "expression_title": expression_title,
            },
        )

    # -- SPARQL ------------------------------------------------------------------------

    def _page_query(self, window: _Window, limit: int, after: str | None) -> str:
        """Build the query listing one page of a window.

        The page is taken by **keyset**: ordered by ``?celex`` and asking only for the numbers
        above the last one the previous page returned. The obvious alternative — ``ORDER BY
        ?modified_at ?celex`` with ``LIMIT``/``OFFSET`` — is what this connector used until
        it was measured, and it loses cases. ``?modified_at`` is an aggregate alias
        (``MAX(?modified)``), and ordering a grouped result by one is not a stable total
        order on this endpoint: consecutive offsets are sorted independently, so some CELEX
        come back twice and others never come back at all. Against the live endpoint on
        7 August 2026, page size 100: the window 2023-04-01 to 2023-04-16 counts 9,875 cases
        and returned 9,875 rows holding 8,139 distinct CELEX — 1,736 cases, 17.6% of the
        window, silently absent. The same window paged as below returns 9,875 distinct rows.

        ``?celex`` is the ``GROUP BY`` key, so it is unique per row and a total order by
        itself, and a keyset page cannot skip or repeat whatever the endpoint does with its
        query plan. It also avoids a deep ``OFFSET`` over two ``OPTIONAL`` expression joins,
        which is what made the far end of a large window expensive enough to time out.

        Args:
            window: The window to enumerate.
            limit: Page size.
            after: CELEX number the previous page ended on, or ``None`` for the first page.
                It comes from a source payload, so unlike everything else interpolated into
                a query it is checked here rather than trusted — :func:`_paging_key` has
                already refused anything that is not a CELEX number, and a CELEX number can
                carry no quote, backslash, angle bracket or whitespace.

        Returns:
            The SPARQL query.
        """
        preferred = _authority_uri("language", self.settings.eurlex_languages[0])
        gate = f'  FILTER(STR(?celex) > "{_celex_literal(after)}")\n' if after else ""
        return (
            f"{_PREFIXES}"
            "SELECT ?celex (MAX(?modified) AS ?modified_at) (MIN(?date) AS ?document_date)\n"
            "       (SAMPLE(?preferred) AS ?title) (SAMPLE(?any) AS ?fallback_title)\n"
            "WHERE {\n"
            f"{self._body(window)}"
            f"{gate}"
            "  OPTIONAL {\n"
            "    ?preferred_expression cdm:expression_belongs_to_work ?work .\n"
            f"    ?preferred_expression cdm:expression_uses_language {preferred} .\n"
            "    ?preferred_expression cdm:expression_title ?preferred .\n"
            "  }\n"
            "  OPTIONAL {\n"
            "    ?any_expression cdm:expression_belongs_to_work ?work .\n"
            "    ?any_expression cdm:expression_title ?any .\n"
            "  }\n"
            "}\n"
            "GROUP BY ?celex\n"
            "ORDER BY ?celex\n"
            f"LIMIT {int(limit)}\n"
        )

    def _count_query(self, window: _Window) -> str:
        """Build the query counting the cases in a window.

        Args:
            window: The window to count.

        Returns:
            The SPARQL query.
        """
        return (
            f"{_PREFIXES}"
            "SELECT (COUNT(DISTINCT ?celex) AS ?total)\n"
            "WHERE {\n"
            f"{self._body(window)}"
            "}\n"
        )

    def _body(self, window: _Window) -> str:
        """Build the graph pattern both discovery queries share.

        Every value interpolated here is either a typed literal rendered from a
        :class:`datetime` or a controlled-vocabulary code that :class:`~plt.config.Settings`
        has restricted to letters, digits and underscores and :func:`_authority_uri` checks
        again. Nothing from a source payload or a request reaches this pattern at all. The
        one source-derived value that reaches a query anywhere on this connector is the
        keyset page gate, which :func:`_paging_key` and :func:`_celex_literal` between them
        restrict to a CELEX number — that, and this, is what closes SPARQL injection here.

        Args:
            window: The window to bound the pattern by.

        Returns:
            The shared ``WHERE`` body, indented, ending in a newline.
        """
        types = " ".join(
            _authority_uri("resource-type", code) for code in self.settings.eurlex_resource_types
        )
        if self.settings.eurlex_discovery_date is EurLexDiscoveryDate.DOCUMENT:
            bound = (
                "  ?work cdm:work_date_document ?date .\n"
                f"  FILTER(?date >= {_date_literal(window.start)}"
                f" && ?date < {_date_literal(window.stop)})\n"
                "  OPTIONAL { ?work cmr:lastModificationDate ?modified }\n"
            )
        else:
            bound = (
                "  ?work cmr:lastModificationDate ?modified .\n"
                f"  FILTER(?modified >= {_instant_literal(window.start)}"
                f" && ?modified < {_instant_literal(window.stop)})\n"
                "  OPTIONAL { ?work cdm:work_date_document ?date }\n"
            )
        return (
            "  ?work cdm:resource_legal_id_celex ?celex .\n"
            f'  ?work cdm:resource_legal_id_sector "{_CASE_LAW_SECTOR}"^^xsd:string .\n'
            f"  VALUES ?resource_type {{ {types} }}\n"
            "  ?work cdm:work_has_resource-type ?resource_type .\n"
            f"{bound}"
        )

    def _count(self, window: _Window) -> int:
        """Count the cases in a window.

        Args:
            window: The window to count.

        Returns:
            The number of distinct CELEX numbers in it.
        """
        rows = self._query(self._count_query(window))
        if not rows:
            return 0
        total = rows[0].get("total", {}).get("value")
        try:
            return int(total) if total is not None else 0
        except ValueError:  # pragma: no cover - the endpoint types this binding as an integer
            return 0

    def _query(self, query: str) -> list[dict[str, dict[str, str]]]:
        """Send one SPARQL query and return its binding sets.

        Args:
            query: The query text.

        Returns:
            One mapping per result row, in SPARQL JSON results form.

        Raises:
            SourceUnavailableError: If the endpoint fails, or answers with something that is
                not a result set. Discovery is all or nothing: a query that cannot be read
                leaves a window half-enumerated, which must never be mistaken for an empty
                one and silently checkpointed past.
        """
        try:
            response = self._client.request(
                "POST",
                self.settings.eurlex_sparql_url,
                data={"query": query, "format": "application/sparql-results+json"},
                headers={"Accept": "application/sparql-results+json"},
            )
        except DocumentUnavailableError as error:
            message = f"the CELLAR SPARQL endpoint rejected a discovery query: {error}"
            raise SourceUnavailableError(message) from error
        try:
            bindings = response.json()["results"]["bindings"]
        except (ValueError, KeyError, TypeError) as error:
            message = f"the CELLAR SPARQL endpoint returned an unreadable result set: {error}"
            raise SourceUnavailableError(message) from error
        if not isinstance(bindings, list):  # pragma: no cover - defensive
            message = "the CELLAR SPARQL endpoint returned a result set without bindings"
            raise SourceUnavailableError(message)
        return bindings

    # -- fetching ----------------------------------------------------------------------

    def _celex_url(self, celex: str) -> str:
        """Return the CELLAR REST URL of a CELEX number.

        The number is percent-encoded into the path. That is not decoration: a corrigendum
        or a second order in the same case carries a parenthesised suffix
        (``62021TO0601(01)``), and CELLAR answers the unencoded form with a 404 while
        serving ``62021TO0601%2801%29`` (verified 4 August 2026). Those documents would
        otherwise fail every week and hold the checkpoint back for the whole window.

        Args:
            celex: The CELEX number, already validated.

        Returns:
            The URL the notice and the manifestations are content-negotiated at.
        """
        base = self.settings.eurlex_cellar_base_url.rstrip("/")
        return f"{base}/{quote(celex, safe='')}"

    def fetch(self, candidate: Candidate) -> RawDocument:
        """Retrieve one case: its metadata notice and the chosen language manifestations.

        The notice is the payload; the manifestations travel in ``source_metadata`` so that
        each keeps its own verbatim body for its own ``case_document`` row. A manifestation
        CELLAR does not hold is logged and skipped — a decision whose full text is not
        published still has a notice worth storing, and the run must not stop over it.

        When none of the preferred languages yields a text, the fallbacks are tried one at a
        time until one does. That is not theoretical: ``62022TJ0371`` has an English
        expression whose manifestation answers ``500`` every time while the French and German
        ones are served normally (verified 4 August 2026).

        Args:
            candidate: The candidate to retrieve.

        Returns:
            The notice and the manifestations, unmodified.

        Raises:
            DocumentUnavailableError: If the CELEX number is malformed, if the notice is
                missing or unreadable, or if every language version of a case that has one
                failed with a server error. The runner then retries the case next run rather
                than storing it without the text it should have had.
            SourceUnavailableError: If CELLAR itself has become unusable.
        """
        celex = _validate_celex(candidate.source_id)
        url = self._celex_url(celex)
        response = self._client.get(url, headers={"Accept": _NOTICE_ACCEPT})
        notice = _parse(response.content, what="notice", source_id=celex)
        preferred, fallbacks = self._language_plan(notice, celex)
        broken: list[str] = []
        manifestations = [
            manifestation
            for language, reason in preferred
            if (manifestation := self._manifestation(celex, language, reason, broken)) is not None
        ]
        if not manifestations:
            for language, reason in fallbacks:
                manifestation = self._manifestation(celex, language, reason, broken)
                if manifestation is not None:
                    manifestations.append(manifestation)
                    break
        if manifestations and broken:
            log.warning(
                "a language version could not be served; another one was used instead",
                extra={"context": {"source_id": celex, "failed_languages": broken}},
            )
        if not manifestations:
            if broken:
                # Not "this decision has no full text" but "CELLAR could not serve the one it
                # has". Storing it as text-free would file that difference away silently and
                # never look again, because nothing about the case would have changed.
                message = (
                    f"{celex}: every language version failed with a server error "
                    f"({', '.join(broken)}); leaving it for the next run"
                )
                raise DocumentUnavailableError(message)
            log.info(
                "no full text retrieved; keeping the notice alone",
                extra={
                    "context": {
                        "source_id": celex,
                        "languages": [code for code, _ in (*preferred, *fallbacks)],
                    }
                },
            )
        return RawDocument(
            candidate=candidate,
            payload=response.text,
            media_format="xml",
            source_url=str(response.url),
            source_metadata={
                "notice_url": url,
                "notice_content_type": response.headers.get("content-type"),
                "manifestations": manifestations,
            },
        )

    def _language_plan(
        self, notice: etree._Element, celex: str
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """Decide which language versions to retrieve, and in what order to fall back.

        The preferred languages are whatever ``eurlex_languages`` names — English by default
        — restricted to the languages the notice says exist, so content negotiation is never
        asked for one that does not. The fallbacks are the procedural language and then one
        more, tried only if the preferred ones yield nothing: that is what saves a
        French-only order, and a case whose English manifestation CELLAR cannot serve.

        Args:
            notice: The parsed object notice.
            celex: CELEX number, for the log line.

        Returns:
            The preferred languages, all of which are retrieved, and the fallbacks, of which
            at most one is. Each is a pair of ISO 639-3 code and the reason it was chosen;
            the reason is recorded on the document row, so a reader can tell a deliberate
            English version from a fallback.
        """
        available = _available_languages(notice)
        preferred = [
            (code, "preferred") for code in self.settings.eurlex_languages if code in available
        ]
        tried = {code for code, _ in preferred}
        fallbacks: list[tuple[str, str]] = []
        procedural = _procedure_language(notice)
        if procedural and procedural in available and procedural not in tried:
            fallbacks.append((procedural, "procedural"))
            tried.add(procedural)
        for code in available:
            if code not in tried:
                # One more, not all 24: a case CELLAR holds no document for at all would
                # otherwise cost two dozen requests to establish that.
                fallbacks.append((code, "only_available"))
                break
        if not preferred and not fallbacks:
            log.info(
                "the notice lists no language version", extra={"context": {"source_id": celex}}
            )
        return preferred, fallbacks

    def _manifestation(
        self,
        celex: str,
        language: str,
        reason: str,
        broken: list[str],
    ) -> dict[str, Any] | None:
        """Retrieve one language version of the full text.

        Args:
            celex: CELEX number, already validated.
            language: ISO 639-3 code CELLAR knows the version by.
            reason: Why this language was chosen, recorded on the document row.
            broken: Languages that failed with a server error, appended to. The caller uses
                it to tell "this decision has no full text" from "CELLAR could not serve the
                one it has", which are stored very differently.

        Returns:
            The manifestation, or ``None`` when CELLAR does not hold it or could not serve it.
        """
        try:
            response = self._client.get(
                self._celex_url(celex),
                headers={"Accept": _TEXT_ACCEPT, "Accept-Language": language.lower()},
            )
        except DocumentUnavailableError as error:
            log.info(
                "no manifestation in this language",
                extra={"context": {"source_id": celex, "language": language, "reason": str(error)}},
            )
            return None
        except SourceUnavailableError as error:
            # The notice for this same CELEX was served moments ago, so CELLAR is up and this
            # is one broken document rather than an outage. Recorded and passed over, so the
            # next language gets its turn; if none of them works the caller raises.
            broken.append(language)
            log.warning(
                "a language version could not be served",
                extra={"context": {"source_id": celex, "language": language, "error": str(error)}},
            )
            return None
        return {
            "language": language,
            "selected_as": reason,
            "payload": response.text,
            "media_format": _media_format(response.headers.get("content-type")),
            "source_url": str(response.url),
            "content_type": response.headers.get("content-type"),
        }

    # -- normalisation -----------------------------------------------------------------

    def normalise(self, raw: RawDocument) -> NormalisedCase:
        """Map a notice and its manifestations onto the schema.

        Args:
            raw: The payloads returned by :meth:`fetch`.

        Returns:
            The case, carrying one document per retrieved language plus the notice itself,
            the citations the notice records, and every remaining CDM field in
            ``source_metadata``.

        Raises:
            DocumentUnavailableError: If the notice cannot be parsed or carries no ``WORK``.
        """
        celex = _validate_celex(raw.source_id)
        root = _parse(raw.payload.encode("utf-8"), what="notice", source_id=celex)
        work = root.find("WORK")
        if work is None:
            message = f"{celex}: the notice carries no WORK element"
            raise DocumentUnavailableError(message)

        resource_type = _find_text(work, "WORK_HAS_RESOURCE-TYPE/URI/IDENTIFIER")
        documents = self._documents(raw, resource_type)
        procedural = _procedure_language(root)
        # Discovery's title is bound to this work's own expression. The notice's is not: an
        # object notice embeds the Official Journal container's expression, and for an
        # Advocate General's opinion that is the *judgment's* title, which would file one
        # document's name against another's. The notice's title is therefore only the
        # fallback, for a case fetched outside a discovery run.
        title = raw.candidate.title or _clean_title(_find_text(root, ".//EXPRESSION_TITLE/VALUE"))
        primary = next(
            (document.language for document in documents if document.has_text),
            _iso_639_1(procedural),
        )
        return NormalisedCase(
            source_id=celex,
            jurisdiction_code=self.jurisdiction_code,
            source_system="cellar",
            title=title,
            abstract=None,
            subject=_subject(work),
            decision_date=_parse_date(_find_text(work, "WORK_DATE_DOCUMENT/VALUE")),
            filing_date=None,
            publication_date=_publication_date(work),
            case_numbers=_case_numbers(work),
            language=primary,
            # The CDM classification is a policy tree, not the public/private/criminal
            # distinction the schema means. Left unset rather than guessed; the tree itself
            # is kept in source_metadata for whoever maps it later.
            law_domain=None,
            law_subfield=None,
            procedure_type=_find_text(
                work, "CASE-LAW_HAS_TYPE_PROCEDURE_CONCEPT_TYPE_PROCEDURE/PREFLABEL"
            ),
            outcome=None,
            source_url=self._celex_url(celex),
            court=_court(work),
            documents=documents,
            # Parties are read from the segmented title, so it has to be the unsplit one:
            # discovery's where there is one, the notice's otherwise.
            parties=_parties(
                raw.candidate.source_metadata.get("expression_title")
                or _find_text(root, ".//EXPRESSION_TITLE/VALUE")
            ),
            citations=_citations(work),
            source_metadata=_metadata(root, work, raw, procedural, resource_type),
        )

    def _documents(
        self,
        raw: RawDocument,
        resource_type: str | None,
    ) -> tuple[NormalisedDocument, ...]:
        """Build the document rows: one per retrieved language, plus the notice itself.

        This is where "one case per CELEX" is made true in the data: several language
        versions of one decision are several rows here and one ``case`` row above.

        Args:
            raw: The payloads returned by :meth:`fetch`.
            resource_type: CDM resource type of the work, deciding the document kind.

        Returns:
            The documents, language versions first.
        """
        kind = _DOCUMENT_TYPES.get(resource_type or "", DocumentType.OTHER)
        documents: list[NormalisedDocument] = []
        manifestations = raw.source_metadata.get("manifestations") or []
        for manifestation in manifestations:
            language = str(manifestation.get("language") or "")
            payload = str(manifestation.get("payload") or "")
            documents.append(
                NormalisedDocument(
                    doc_type=kind,
                    language=_iso_639_1(language),
                    full_text=_extract_text(payload, raw.source_id),
                    raw_payload=payload,
                    media_format=str(manifestation.get("media_format") or "html"),
                    source_url=manifestation.get("source_url"),
                    source_metadata={
                        "cellar_language": language,
                        "selected_as": manifestation.get("selected_as"),
                        "content_type": manifestation.get("content_type"),
                        "resource_type": resource_type,
                    },
                )
            )
        documents.append(
            NormalisedDocument(
                # The metadata notice is not a court document. It is kept verbatim so that a
                # later reclassification never needs a re-fetch (architecture rule 2.6), and
                # carries no extracted text, so the filter chain reads judgments rather than
                # markup.
                doc_type=DocumentType.OTHER,
                language=None,
                full_text=None,
                raw_payload=raw.payload,
                media_format=raw.media_format or "xml",
                source_url=raw.source_url,
                source_metadata={"notice": "object", "resource_type": resource_type},
            )
        )
        return tuple(documents)

    def close(self) -> None:
        """Close the HTTP client, whether the run finished or was interrupted."""
        self._client.close()


# --------------------------------------------------------------------------------------
# Notice readers. Free functions rather than methods: they depend on the CDM notice format
# and on nothing about the connector's configuration, so they can be tested against a
# recorded fixture on their own.
# --------------------------------------------------------------------------------------


def _work_of(notice: etree._Element) -> etree._Element:
    """Return the ``WORK`` element of a notice, or the element itself.

    An object notice embeds the notices of neighbouring works — the Official Journal
    container, the dossier, the events — and their elements have the same names as this
    work's. Every reader below therefore looks at the direct children of *this* ``WORK``
    rather than at every descendant of the notice, so one decision cannot inherit another's
    citations, descriptors or case numbers.

    Args:
        notice: The parsed notice, or a ``WORK`` element already.

    Returns:
        The work element to read from.
    """
    work = notice.find("WORK")
    return work if work is not None else notice


def _available_languages(notice: etree._Element) -> list[str]:
    """List the languages a work exists in, from its expression links.

    Every ``WORK_HAS_EXPRESSION`` names its expression by a CELEX or ECLI identifier with the
    ISO 639-3 code appended (``62017CJ0616.DAN``), which is the cheapest reliable statement of
    what CELLAR holds: no extra request, and it is what the manifestation requests are then
    limited to, so content negotiation is never asked for a language that does not exist.

    Args:
        notice: The parsed object notice.

    Returns:
        The codes, sorted, without duplicates.
    """
    codes: set[str] = set()
    for link in _work_of(notice).findall("WORK_HAS_EXPRESSION"):
        for identifier in link.findall("SAMEAS/URI/IDENTIFIER"):
            text = _text_of(identifier)
            if not text or "." not in text:
                continue
            suffix = text.rsplit(".", 1)[-1]
            if len(suffix) == 3 and suffix.isalpha():
                codes.add(suffix.upper())
    return sorted(codes)


def _procedure_language(notice: etree._Element) -> str | None:
    """Return the language the case was argued in.

    Args:
        notice: The parsed object notice.

    Returns:
        The ISO 639-3 code, or ``None``.
    """
    for element in _work_of(notice).findall("CASE-LAW_USES_PROCEDURE_LANGUAGE"):
        code = _find_text(element, "URI/IDENTIFIER") or _find_text(element, "IDENTIFIER")
        if code:
            return code.upper()
    return None


def _iso_639_1(code: str | None) -> str | None:
    """Map an ISO 639-3 language code onto the ISO 639-1 code the schema stores.

    Args:
        code: The three-letter code, or ``None``.

    Returns:
        The two-letter code, the lower-cased original when it is not one of the 24 official
        languages, or ``None``.
    """
    if not code:
        return None
    return _ISO_639_1.get(code.upper(), code.lower())


def _media_format(content_type: str | None) -> str:
    """Name the payload format for the ``case_document.format`` column.

    Args:
        content_type: The response's content type, if it stated one.

    Returns:
        ``xhtml``, ``html``, ``xml`` or ``text``.
    """
    value = (content_type or "").lower()
    if "xhtml" in value:
        return "xhtml"
    if "html" in value:
        return "html"
    if "xml" in value:
        return "xml"
    return "text"


def _clean_title(title: str | None) -> str | None:
    """Turn a CELLAR expression title into a readable one.

    CELLAR joins the segments of a title with ``#``: court and date, parties, subject-matter
    keywords, case number. All of them are kept — the keyword filter scores the title, and
    the keywords segment is where a case says "Placing of plant protection products on the
    market" — but the separator becomes something a reader can look at.

    Args:
        title: The raw title, or ``None``.

    Returns:
        The title with its segments separated by a space, or ``None``.
    """
    if not title:
        return None
    return _collapse(title.replace(_TITLE_SEPARATOR, " ")) or None


def _subject(work: etree._Element) -> str | None:
    """Join the subject-matter and descriptor labels into the scored ``subject`` field.

    ``data/keywords/eu.json`` weights ``subject`` above plain full text, and the CDM
    descriptors are where a pesticide case says so in as many words ("Plant protection
    products", "Chemicals"), so this is a strong signal rather than a decorative field.

    Args:
        work: The notice's ``WORK`` element.

    Returns:
        The distinct labels, joined with "; ", or ``None``.
    """
    labels: dict[str, None] = {}
    for name in _SUBJECT_ELEMENTS:
        labels.update(dict.fromkeys(_labels(work, name)))
    return "; ".join(labels) or None


def _case_numbers(work: etree._Element) -> tuple[str, ...]:
    """Read the case numbers a decision is filed under.

    CELLAR files joined cases both individually and as one combined identifier
    ("T-429/13, T-451/13"), so the combined form is split: a reader searching for T-451/13
    should find the judgment that decided it.

    Args:
        work: The notice's ``WORK`` element.

    Returns:
        The numbers ("C-616/17"), in notice order, without duplicates.
    """
    numbers: dict[str, None] = {}
    for link in work.findall("PART_OF"):
        for uri in link.findall("SAMEAS/URI"):
            if _find_text(uri, "TYPE") != "case":
                continue
            identifier = _find_text(uri, "IDENTIFIER")
            for number in (identifier or "").split(","):
                if number.strip():
                    numbers.setdefault(number.strip(), None)
    return tuple(numbers)


def _court(work: etree._Element) -> NormalisedCourt | None:
    """Resolve the court from the corporate body CELLAR credits the decision to.

    Args:
        work: The notice's ``WORK`` element.

    Returns:
        The court — the Court of Justice, the General Court or the Civil Service Tribunal —
        or ``None`` when the notice names no corporate body. It is identified by its
        authority URI rather than by its name, so a renamed court does not become a second
        row. The level and the domain are left unset rather than guessed, and the raw
        ``source_type`` is CELLAR's own ``corporate-body``: the notice types the deciding
        body against the authority table it comes from and states nothing finer, so that is
        what is recorded. It is stored rather than inferred for the same reason as in the
        Dutch connector — the notice says it once — even though, unlike Rechtspraak's
        vocabulary, it does not distinguish one Union court from another.
    """
    for agent in work.findall("WORK_CREATED_BY_AGENT"):
        uri = agent.find("URI")
        agent_type = _find_text(uri, "TYPE") if uri is not None else None
        if uri is None or agent_type != "corporate-body":
            continue
        code = _find_text(uri, "IDENTIFIER")
        name = _find_text(agent, "PREFLABEL")
        if not code or not name:
            continue
        identifier = _find_text(uri, "VALUE") or f"{_AUTHORITY}/corporate-body/{code}"
        return NormalisedCourt(
            source_identifier=identifier,
            name=name,
            source_type=agent_type,
            abbreviation=code,
            source_url=_find_text(uri, "VALUE"),
        )
    return None


def _publication_date(work: etree._Element) -> date | None:
    """Read the date the record was first published in CELLAR.

    Args:
        work: The notice's ``WORK`` element.

    Returns:
        The date part of the repository's creation timestamp, or ``None``. That is when the
        decision became publicly retrievable, which is what ``case.publication_date`` means
        here; the Official Journal notice about the case is a separate CELEX and is not it.
    """
    created = _parse_instant(_find_text(work, "CREATIONDATE/VALUE"))
    if created is not None:
        return created.date()
    return _parse_date(_find_text(work, "DATE_CREATION_LEGACY/VALUE"))


def _parties(title: str | None) -> tuple[NormalisedParty, ...]:
    """Read the litigating parties out of the case title.

    CELLAR exposes no party field, and CJEU titles name the parties in a segment of their
    own: "Bayer CropScience AG and Others v European Commission", "Criminal proceedings
    against Mathieu Blaise and Others". Where the title uses the " v " convention the sides
    are recorded as applicant and defendant; where it does not, the segment is kept as one
    party whose role stays ``other`` rather than being invented.

    Args:
        title: The raw, ``#``-separated title, or ``None``.

    Returns:
        The parties, in the order the title names them.
    """
    if not title:
        return ()
    segments = [segment.strip(" .") for segment in title.split(_TITLE_SEPARATOR)]
    if len(segments) < 2 or not segments[1]:
        return ()
    segment = _collapse(segments[1])
    sides = [side.strip(" .") for side in _PARTY_SEPARATOR.split(segment)]
    sides = [side for side in sides if side]
    if len(sides) < 2:
        return (NormalisedParty(name=segment, role=PartyRole.OTHER, ordinal=1),)
    roles = [PartyRole.APPLICANT, *[PartyRole.DEFENDANT] * (len(sides) - 1)]
    return tuple(
        NormalisedParty(name=name, role=role, ordinal=index + 1)
        for index, (name, role) in enumerate(zip(sides, roles, strict=True))
    )


def _citations(work: etree._Element) -> tuple[NormalisedCitation, ...]:
    """Read the instruments and decisions a judgment cites.

    Args:
        work: The notice's ``WORK`` element.

    Returns:
        One citation per distinct target and relation, identified by CELEX where CELLAR gives
        one and by ECLI otherwise. This is the table an EU pesticide-regulation citation
        graph is later built from, so the relation CELLAR recorded — cites, interprets,
        declares valid — is preserved rather than flattened.
    """
    citations: dict[tuple[str, str], NormalisedCitation] = {}
    for element_name, relation in _CITATION_RELATIONS.items():
        for link in work.findall(element_name):
            identifiers = {
                (_find_text(uri, "TYPE") or ""): _find_text(uri, "IDENTIFIER")
                for uri in link.findall("SAMEAS/URI")
            }
            target = identifiers.get("celex") or identifiers.get("ecli")
            if not target:
                continue
            key = (target, relation)
            if key in citations:
                continue
            citations[key] = NormalisedCitation(
                target_identifier=target,
                citation_type=relation,
                target_scheme="celex" if identifiers.get("celex") else "ecli",
                target_url=_find_text(link, "URI/VALUE"),
                source_metadata={
                    "identifiers": {
                        scheme: value for scheme, value in identifiers.items() if scheme and value
                    }
                },
            )
    return tuple(citations.values())


def _metadata(
    root: etree._Element,
    work: etree._Element,
    raw: RawDocument,
    procedural: str | None,
    resource_type: str | None,
) -> dict[str, Any]:
    """Collect everything the notice exposed that has no column of its own.

    Args:
        root: The parsed notice.
        work: Its ``WORK`` element.
        raw: The payloads, for the URLs they came from.
        procedural: Procedural language of the case.
        resource_type: CDM resource type of the work.

    Returns:
        The mapping stored as ``case.source_metadata``.
    """
    manifestations = raw.source_metadata.get("manifestations") or []
    return {
        "celex": _find_text(work, "RESOURCE_LEGAL_ID_CELEX/VALUE"),
        "ecli": _find_text(work, "ECLI/VALUE"),
        "cellar_uri": _find_text(work, "URI/VALUE"),
        "cellar_identifier": _find_text(work, "URI/IDENTIFIER"),
        "resource_type": resource_type,
        "resource_type_label": _find_text(work, "WORK_HAS_RESOURCE-TYPE/PREFLABEL"),
        "celex_type": _find_text(work, "RESOURCE_LEGAL_TYPE/VALUE"),
        "court_formation": _find_text(work, "CASE-LAW_DELIVERED_BY_COURT-FORMATION/PREFLABEL"),
        "procedure_code": _find_text(
            work, "CASE-LAW_HAS_TYPE_PROCEDURE_CONCEPT_TYPE_PROCEDURE/URI/IDENTIFIER"
        ),
        "originates_in_country": _find_text(work, "CASE-LAW_ORIGINATES_IN_COUNTRY/PREFLABEL"),
        "appealed_in_case": _find_text(
            work, "CASE-LAW_SUBJECT_TO_APPEAL_IN_CASE_COURT/SAMEAS/URI/IDENTIFIER"
        ),
        "national_judgement": _find_text(work, "CASE-LAW_NATIONAL-JUDGEMENT/VALUE"),
        "date_request_opinion": _find_text(work, "RESOURCE_LEGAL_DATE_REQUEST_OPINION/VALUE"),
        "date_creation_legacy": _find_text(work, "DATE_CREATION_LEGACY/VALUE"),
        "cellar_creation_date": _find_text(work, "CREATIONDATE/VALUE"),
        "cellar_last_modification_date": _find_text(work, "LASTMODIFICATIONDATE/VALUE"),
        "work_version": _find_text(work, "VERSION/VALUE"),
        "published_in_reports": _find_text(work, "CASE-LAW_PUBLISHED_IN_ERECUEIL/VALUE"),
        "subject_matter": _labels(work, "RESOURCE_LEGAL_IS_ABOUT_SUBJECT-MATTER"),
        "case_law_subject_matter": _labels(work, "CASE-LAW_IS-ABOUT_CASE-LAW-SUBJECT-MATTER"),
        "case_law_directory_codes": _codes(work, "CASE-LAW_IS_ABOUT_CONCEPT"),
        "case_law_directory_labels": _labels(work, "CASE-LAW_IS_ABOUT_CONCEPT"),
        "eurovoc_descriptors": _labels(work, "IS_ABOUT"),
        "available_languages": _available_languages(root),
        "procedure_language": procedural,
        "retrieved_languages": [
            {
                "language": manifestation.get("language"),
                "selected_as": manifestation.get("selected_as"),
                "source_url": manifestation.get("source_url"),
            }
            for manifestation in manifestations
        ],
        "notice_decoding": root.get("decoding"),
        "notice_url": raw.source_metadata.get("notice_url"),
    }


def _labels(work: etree._Element, element_name: str) -> list[str]:
    """List the preferred labels below every occurrence of an element of this work.

    The element itself is a direct child of ``WORK`` — an embedded neighbouring work's
    descriptors are not this decision's — but the labels below it may be nested, as the
    hierarchical directory codes are.

    Args:
        work: The notice's ``WORK`` element.
        element_name: Element to search for.

    Returns:
        The distinct labels, in notice order.
    """
    labels: dict[str, None] = {}
    for element in work.findall(element_name):
        for label in element.iter("PREFLABEL"):
            text = _text_of(label)
            if text:
                labels.setdefault(text, None)
    return list(labels)


def _codes(work: etree._Element, element_name: str) -> list[str]:
    """List the vocabulary codes below every occurrence of an element of this work.

    Args:
        work: The notice's ``WORK`` element.
        element_name: Element to search for.

    Returns:
        The distinct codes, in notice order.
    """
    codes: dict[str, None] = {}
    for element in work.findall(element_name):
        for code in element.iter("OP-CODE"):
            text = _text_of(code)
            if text:
                codes.setdefault(text, None)
    return list(codes)


def _extract_text(payload: str, source_id: str) -> str | None:
    """Extract the readable text of a manifestation.

    A CJEU manifestation is XHTML whose doctype references an external DTD, and is parsed
    with entity resolution, DTD loading and network access all disabled. Older judgments are
    served as loose HTML instead, which the XML parser refuses; those fall back to
    :mod:`lxml`'s HTML parser, configured the same way.

    Args:
        payload: The manifestation body.
        source_id: CELEX number, for the log line.

    Returns:
        The text with its whitespace collapsed, or ``None`` when nothing could be read.
    """
    if not payload.strip():
        return None
    data = payload.encode("utf-8")
    try:
        root: etree._Element = etree.fromstring(data, parser=_safe_parser())
    except etree.XMLSyntaxError:
        parser = etree.HTMLParser(no_network=True, huge_tree=False, recover=True)
        try:
            recovered = etree.fromstring(data, parser=parser)
        except etree.XMLSyntaxError as error:
            log.warning(
                "a manifestation could not be parsed; keeping it without extracted text",
                extra={"context": {"source_id": source_id, "error": str(error)}},
            )
            return None
        if recovered is None:  # pragma: no cover - recover=True returns a tree for any input
            return None
        root = recovered
    return _collapse(" ".join(_iter_text(root))) or None


def _iter_text(root: etree._Element) -> Iterable[str]:
    """Yield the text of every element, skipping what a reader never sees.

    Args:
        root: Root of the parsed document.

    Yields:
        Text fragments, in document order.
    """
    # ``"*"`` iterates elements only, so an XML comment or processing instruction cannot
    # smuggle its text into a judgment's body.
    for element in root.iter("*"):
        if str(element.tag).rsplit("}", 1)[-1].lower() in _INVISIBLE_TAGS:
            continue
        if element.text:
            yield element.text
        if element.tail:
            yield element.tail
