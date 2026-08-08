"""Netherlands connector: Rechtspraak.nl Open Data.

The Raad voor de Rechtspraak publishes the whole of Dutch case law — rechtbanken,
gerechtshoven, the Centrale Raad van Beroep, the College van Beroep voor het bedrijfsleven,
the Raad van State and the Hoge Raad — through one open portal, and this connector reads all
of it. Most pesticide litigation is first-instance, so restricting the connector to the apex
courts would miss the bulk of the corpus.

Three endpoints, all resolved from :class:`~plt.config.Settings` and none hard-coded:

* ``settings.rechtspraak_search_url`` — an Atom feed, windowed on ``modified``, sorted with
  ``sort=ASC`` and sliced with ``max`` (at most 1000) and ``from``. The ``subtitle`` element
  carries the total number of hits for the window, which is what :meth:`discover` sizes a
  window against so that it never has to use ``from`` at all; see its docstring for why.
* ``settings.rechtspraak_content_url`` — one document, as Rechtspraak XML: a Dublin Core
  ``rdf:Description`` metadata block, an optional ``inhoudsindicatie`` (abstract) and an
  optional ``uitspraak`` or ``conclusie`` body.
* ``settings.rechtspraak_vocabulary_url`` — the controlled vocabularies. ``Instanties`` seeds
  the ``court`` table and supplies each court's level, domain and abbreviation, so no court
  name is written into the code.

**There is no full-text search on this API.** Nothing in the query string can express
"pesticides", which is why topical selection happens client-side in the keyword filter
(``docs/core-document.md`` section 2.5) and why this connector's job is to hand the filter
every scrap of text and classification the endpoint exposes.

Five properties of the endpoint drove the design, each verified against the live service on
4 August 2026 unless another date is given:

1. **``modified`` is expressed in Europe/Amsterdam local time, while the feed's Atom
   ``updated`` is UTC.** A ``Z`` or an offset in the parameter is ignored rather than
   honoured. The window is therefore converted to Dutch local time on the way out and every
   entry is checked again against the caller's UTC bounds on the way in, so the window a
   caller asked for is the window it gets.
2. **``return=DOC`` restricts the feed to the ECLIs that carry text, and ``DOC`` is the only
   value the parameter accepts** — ``META`` is answered with a ``400``. Excluding the rest is
   a deliberate decision under the no-false-negatives policy of ``docs/core-document.md``
   section 2.7, and it rests on two measurements rather than on convenience. First, the
   excluded records carry **no text at all**: 42 of 42 sampled across 2015, 2020 and 2026 had
   neither an ``inhoudsindicatie`` nor a body, a median of 1.8 kB of registration metadata
   each. No stage that reads text could ever select one, so leaving them out cannot lose a
   case that would otherwise have been found. Second, they are not merely *not published yet*:
   the share is between 61 and 78% and flat for decision dates from 2015 to 2026, so an
   eleven-year-old registration is still a registration. Should one ever gain a text, its
   ``modified`` advances and it enters this feed in a later incremental window, so the design
   recovers that case for free. Including them would cost roughly 11,500 further requests a
   month on a public court endpoint for no attainable match. The setting
   ``rechtspraak_documents_only`` makes it an operator's decision all the same.
3. **The Atom ``updated`` is a revision marker.** It goes on the candidate as its
   ``content_hash``, so the weekly re-run skips an unchanged document *without fetching it*
   (``docs/architecture.md`` section 4.2).
4. **A document may carry no body.** Metadata-only ECLIs and documents whose text was
   withdrawn are normalised into a case with a ``case_document`` row holding the raw XML and
   no ``full_text``, so nothing is lost, nothing crashes, and the filter is never handed an
   empty string to score.
5. **``sort=ASC`` is not a total order, and there is no key to page by** (8 August 2026). It
   sorts on ``updated``, which is not unique — 950,077 text-bearing ECLIs share far fewer
   distinct timestamps than that, and a tie's internal order is the endpoint's to choose. The
   feed offers ``from`` and ``max`` and nothing to page *after*, so the EU connector's cure
   for the same defect is unavailable here. What is available is the ``subtitle`` count and
   both ``modified`` bounds being inclusive to the second: a window can be measured before it
   is read, and split at a second boundary, which no tie can straddle. :meth:`discover`
   therefore narrows rather than pages.

XML from a remote endpoint is parsed with entity resolution, DTD loading and network access
all disabled, which is what rules out XXE and entity-expansion attacks; ``xml.etree`` is not
used anywhere in this module.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from lxml import etree

from plt.db.models import DocumentType, LawDomain
from plt.pipeline.base import (
    Candidate,
    DocumentUnavailableError,
    NormalisedCase,
    NormalisedCitation,
    NormalisedCourt,
    NormalisedDocument,
    RawDocument,
    SourceConnector,
    SourceTraffic,
    SourceUnavailableError,
)
from plt.pipeline.dedup import content_hash
from plt.pipeline.http import PoliteClient
from plt.pipeline.windows import Window
from plt.utils.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    from plt.config import Settings

__all__ = [
    "CourtRecord",
    "RechtspraakConnector",
]

log = get_logger(__name__)

# -- Namespaces -----------------------------------------------------------------------

_ATOM: Final[str] = "http://www.w3.org/2005/Atom"
_RDF: Final[str] = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_RDFS: Final[str] = "http://www.w3.org/2000/01/rdf-schema#"
_DCTERMS: Final[str] = "http://purl.org/dc/terms/"
_PSI: Final[str] = "http://psi.rechtspraak.nl/"
_ECLI_NS: Final[str] = "https://e-justice.europa.eu/ecli"
_BWB: Final[str] = "bwb-dl"
_CVDR: Final[str] = "http://decentrale.regelgeving.overheid.nl/cvdr/"
_CELEX: Final[str] = "http://publications.europa.eu/celex/"
_TUCHTRECHT: Final[str] = "http://tuchtrecht.overheid.nl/"
_RECHTSPRAAK: Final[str] = "http://www.rechtspraak.nl/schema/rechtspraak-1.0"

#: Prefixes used when a source element or attribute is recorded in ``source_metadata``.
#: Keeping the source's own prefixes makes the stored block read the same way as the raw XML.
_PREFIXES: Final[Mapping[str, str]] = {
    _RDF: "rdf",
    _RDFS: "rdfs",
    _DCTERMS: "dcterms",
    _PSI: "psi",
    _ECLI_NS: "ecli",
    _BWB: "bwb",
    _CVDR: "cvdr",
    _CELEX: "eu",
    _TUCHTRECHT: "tr",
    _RECHTSPRAAK: "rs",
    _ATOM: "atom",
}

#: Identifier scheme of a ``resourceIdentifier`` attribute, by the prefix that carries it.
#: ``eu:resourceIdentifier`` holds a CELEX number, which is the EU jurisdiction's own key.
_CITATION_SCHEMES: Final[Mapping[str, str]] = {
    "bwb": "bwb",
    "ecli": "ecli",
    "eu": "celex",
    "cvdr": "cvdr",
}

# -- Source protocol constants --------------------------------------------------------

#: Largest page the search endpoint serves. Asking for more is silently clamped, so the
#: connector clamps first and keeps ``rechtspraak_page_size`` an honest description of one
#: request — which matters more than tidiness here, because that setting is also the size a
#: window is narrowed to fit inside.
_MAX_PAGE_SIZE: Final[int] = 1000

#: Timezone the ``modified`` query parameter is read in. The feed's ``updated`` elements are
#: UTC; this parameter is not, and it ignores an explicit offset rather than honouring it.
_SOURCE_TIMEZONE: Final[str] = "Europe/Amsterdam"

#: Widening applied to the requested window when no timezone database is installed. Dutch
#: local time runs one or two hours ahead of UTC, so sending the UTC instant widens the lower
#: bound and this margin widens the upper one. Both ends are then trimmed exactly against the
#: entries' own UTC timestamps, so the window a caller asked for is the window it gets.
_CLOCK_MARGIN: Final[timedelta] = timedelta(hours=2)

#: Where a walk with no lower bound starts. Nothing in the feed is older — the earliest
#: ``modified`` measured on 8 August 2026 was in 2013 — and the walk widens its own windows,
#: so the empty century between the two costs a few dozen requests rather than one a day.
_EARLIEST: Final[datetime] = datetime(1900, 1, 1, tzinfo=UTC)

#: Format the endpoint accepts for a ``modified`` bound; an offset suffix is ignored.
_WINDOW_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%S"

#: Resolution of a ``modified`` bound, and therefore the smallest step between two windows.
#: The parameter is expressed to the second, so a bound *is* a second: adjacent windows that
#: meet at ``stop`` and ``stop + 1s`` cover everything between them and nothing twice, whatever
#: sub-second precision the source keeps internally.
_TICK: Final[timedelta] = timedelta(seconds=1)

#: Fill below which the next window is widened, and above which it is narrowed, as fractions
#: of a page. The gap between them is deliberate: a walk that resized after every window would
#: oscillate across a boundary instead of settling near it.
_GROW_BELOW: Final[float] = 0.25
_SHRINK_ABOVE: Final[float] = 0.75

#: The number the feed states in its ``atom:subtitle``: ``Aantal gevonden ECLI's: 57``. It is
#: the source's own count of the window, arrived at without paging, which is what lets the
#: connector decide whether a window fits in one request before it asks for it.
_STATED_TOTAL: Final[re.Pattern[str]] = re.compile(r"(\d+)")

#: Shape of a European Case Law Identifier. Applied before a candidate costs a request, so a
#: malformed identifier in a feed is reported as one unavailable document rather than sent on
#: to the endpoint.
_ECLI_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\AECLI:[A-Z]{2}:[A-Za-z0-9]{1,10}:[0-9]{4}:[A-Za-z0-9.\-]{1,30}\Z"
)

#: Elements of the Rechtspraak document schema that end a line of extracted text. Inline
#: elements (``emphasis``, ``footnote-ref``) are deliberately absent: breaking a line inside a
#: sentence would let a curated phrase fall across the break and stop matching.
_BLOCK_ELEMENTS: Final[frozenset[str]] = frozenset(
    {
        "bridgehead",
        "entry",
        "footnote",
        "informaltable",
        "listitem",
        "nr",
        "para",
        "parablock",
        "paragroup",
        "row",
        "section",
        "tbody",
        "tgroup",
        "title",
        "uitspraak.info",
    }
)

#: Body elements of the document schema, and the ``case_document`` kind each becomes.
_BODY_ELEMENTS: Final[Mapping[str, DocumentType]] = {
    "uitspraak": DocumentType.JUDGMENT,
    "conclusie": DocumentType.OPINION,
}

#: Top-level *rechtsgebied* to the schema's law domain. The vocabulary at
#: ``Waardelijst/Rechtsgebieden`` has exactly these four roots, and it is hierarchical —
#: ``Bestuursrecht; Vreemdelingenrecht`` gives the domain and the subfield in one value.
_LAW_DOMAINS: Final[Mapping[str, LawDomain]] = {
    "bestuursrecht": LawDomain.PUBLIC,
    "civiel recht": LawDomain.PRIVATE,
    "strafrecht": LawDomain.CRIMINAL,
    "internationaal publiekrecht": LawDomain.PUBLIC,
}

#: ``Instantie`` type from the vocabulary to a court level and subject-matter domain. The
#: types come from the vocabulary itself, so no court is named in this table.
#:
#: **This mapping is lossy, and the loss is why the raw type is stored too.**
#: ``Koninkrijksinstantie`` — the courts of Aruba, Curaçao, Sint Maarten and the BES islands,
#: which are Kingdom territory but not EU territory (``docs/jurisdictions/nl.md`` section 1.1)
#: — flattens onto the same level ``other`` as ``AndereGerechtelijkeInstantie``. The
#: vocabulary states the type once; :attr:`CourtRecord.court_type` therefore reaches
#: ``court.source_type`` unflattened, beside the normalised pair rather than instead of it.
_COURT_TYPES: Final[Mapping[str, tuple[str | None, str | None]]] = {
    "Rechtbank": ("first_instance", None),
    "Kantongerecht": ("first_instance", None),
    "Gerechtshof": ("appeal", None),
    "TypeHr": ("supreme", None),
    "TypeRvS": ("supreme", "administrative"),
    "TypeCRvB": ("supreme", "administrative"),
    "TypeCBb": ("supreme", "administrative"),
    "Parket": ("advisory", None),
    "TuchtrechtelijkeInstantie": ("disciplinary", None),
    "AndereGerechtelijkeInstantie": ("other", None),
    "Koninkrijksinstantie": ("other", None),
    "http://psi.rechtspraak.nl/buitenlandseInstantie": ("foreign", None),
}

#: Separator between several *rechtsgebieden* in the scored ``subject`` field. A character no
#: curated term contains and that the ``\s+`` of a compiled phrase cannot cross, so joining
#: two classifications can never fabricate a match that neither of them contains.
_SUBJECT_SEPARATOR: Final[str] = " | "

#: Collapses the blank lines the document schema's empty ``<para/>`` elements leave behind.
_BLANK_RUN: Final[re.Pattern[str]] = re.compile(r"\n{3,}")

#: Indentation and trailing whitespace around a line break in the extracted text. The source
#: XML is pretty-printed, so without this every extracted line would carry its indentation.
_LINE_PADDING: Final[re.Pattern[str]] = re.compile(r"[ \t]*\n[ \t]*")

#: Byte-order mark the endpoint prefixes its payloads with.
_BOM: Final[bytes] = b"\xef\xbb\xbf"

#: Processing instruction the document schema uses for a hard line break.
_LINEBREAK: Final[str] = "linebreak"


def _parser() -> etree.XMLParser:
    """Build the hardened parser every remote payload is read with.

    Returns:
        A parser with entity resolution, DTD loading, network access and the ``huge_tree``
        relaxations all switched off. Together these close XXE — no external entity is ever
        dereferenced — and the billion-laughs family of entity-expansion attacks, since no
        internal entity is expanded either, and they cap the depth and node count one payload
        can force the process to allocate.
    """
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        recover=False,
    )


def _as_bytes(payload: str | bytes) -> bytes:
    """Return a payload as the bytes the parser wants, byte-order mark removed.

    Args:
        payload: The source response, as text or as bytes.

    Returns:
        UTF-8 bytes. ``lxml`` refuses a ``str`` carrying an encoding declaration — which every
        Rechtspraak payload does — so text is encoded back before parsing, and the endpoint's
        leading byte-order mark is dropped either way.
    """
    if isinstance(payload, str):
        return payload.lstrip("﻿").encode("utf-8")
    if payload.startswith(_BOM):
        return payload[len(_BOM) :]
    return payload


def _parse(payload: str | bytes, *, described_as: str, source_id: str) -> etree._Element:
    """Parse one payload with the hardened parser.

    Args:
        payload: The source response.
        described_as: What the payload is, for the error message.
        source_id: Identifier the payload belongs to, for the error message.

    Returns:
        The root element.

    Raises:
        DocumentUnavailableError: If the payload is not well-formed XML. Scoped to the one
            document, so a single malformed judgment does not end a run over the rest.
    """
    try:
        return etree.fromstring(_as_bytes(payload), parser=_parser())
    except etree.XMLSyntaxError as error:
        message = f"{described_as} for {source_id} is not well-formed XML: {error}"
        raise DocumentUnavailableError(message) from error


def _is_named(node: etree._Element) -> bool:
    """Return whether a node is an element rather than a comment or an instruction.

    Iterating an ``lxml`` element yields its comments and processing instructions alongside
    its child elements, and those carry no tag name. They are still meaningful — a comment's
    or an instruction's *tail* is ordinary document text — so they are recognised here rather
    than filtered out by the iteration.

    Args:
        node: The node to test.

    Returns:
        Whether the node has a tag name.
    """
    return not isinstance(node, etree._Comment | etree._ProcessingInstruction)


def _local_name(node: etree._Element) -> str:
    """Return an element's name without its namespace.

    Args:
        node: The node to name.

    Returns:
        The local name, or the empty string for a comment or a processing instruction.
    """
    if not _is_named(node):
        return ""
    return str(node.tag).rpartition("}")[2]


def _qualified_name(tag: str) -> str:
    """Return a Clark-notation name with the source's own prefix restored.

    Args:
        tag: A tag in ``{namespace}local`` form, or a bare local name.

    Returns:
        ``dcterms:subject`` rather than ``{http://purl.org/dc/terms/}subject``, so the stored
        metadata reads the same way as the raw XML beside it. An unknown namespace is left in
        Clark notation rather than guessed at.
    """
    if not tag.startswith("{"):
        return tag
    namespace, _, local = tag[1:].partition("}")
    prefix = _PREFIXES.get(namespace)
    return f"{prefix}:{local}" if prefix else tag


def _text_of(element: etree._Element) -> str | None:
    """Return an element's own text, whitespace tidied.

    Args:
        element: The element to read.

    Returns:
        The text, or ``None`` when the element carries none of its own. Descendant text is
        deliberately ignored: this reads the flat Dublin Core block, where a descendant means
        a structured value such as ``rdf:list`` rather than more of the same string.
    """
    text = element.text
    if text is None:
        return None
    tidied = " ".join(text.split())
    return tidied or None


def _list_items(element: etree._Element) -> list[str]:
    """Return the ``rdf:li`` values nested under an element.

    Args:
        element: The element to read, typically ``dcterms:hasVersion``.

    Returns:
        One string per list item, in document order, blanks dropped. Multi-valued fields stay
        lists: the *vindplaatsen* of a well-annotated judgment run to a dozen entries and
        flattening them to the first would discard the rest.
    """
    items: list[str] = []
    for item in element.iterfind(f".//{{{_RDF}}}li"):
        value = _text_of(item)
        if value:
            items.append(value)
    return items


def _describe(element: etree._Element) -> dict[str, list[dict[str, Any]]]:
    """Project one ``rdf:Description`` block into plain, storable types.

    Every child element is kept, whether or not this connector understands it, with its text,
    its attributes and any ``rdf:list`` items. A field the schema has no column for therefore
    still reaches ``case.source_metadata``, and a field Rechtspraak adds later is captured
    without a code change — which is what makes a later reclassification possible without
    going back to the courts (``docs/architecture.md`` rule 2.6).

    Args:
        element: The ``rdf:Description`` element to project.

    Returns:
        Mapping of qualified element name to the list of its occurrences, in document order.
        A repeated element — ``psi:zaaknummer``, ``dcterms:subject``, ``dcterms:references`` —
        becomes several entries under one key rather than one value overwriting another.
    """
    described: dict[str, list[dict[str, Any]]] = {}
    for child in element:
        if not _is_named(child):
            continue
        entry: dict[str, Any] = {}
        value = _text_of(child)
        if value is not None:
            entry["value"] = value
        attributes = {_qualified_name(str(name)): str(text) for name, text in child.attrib.items()}
        if attributes:
            entry["attributes"] = attributes
        items = _list_items(child)
        if items:
            entry["items"] = items
        described.setdefault(_qualified_name(str(child.tag)), []).append(entry)
    return described


#: One projected ``rdf:Description``, as :func:`_describe` returns it.
Described = Mapping[str, Sequence[Mapping[str, Any]]]

#: One Atom search entry, reduced to the five fields discovery reads. The parse tree it came
#: from is released as it is read, so a window costs its entries rather than its XML.
Entry = Mapping[str, str | None]


def _values(described: Described, key: str) -> list[str]:
    """Return every text value recorded for one element name.

    Args:
        described: A projected description block.
        key: Qualified element name, e.g. ``psi:zaaknummer``.

    Returns:
        The values, in document order.
    """
    return [str(entry["value"]) for entry in described.get(key, ()) if entry.get("value")]


def _first_value(described: Described, key: str) -> str | None:
    """Return the first text value recorded for one element name, if any.

    Args:
        described: A projected description block.
        key: Qualified element name.

    Returns:
        The first value, or ``None``.
    """
    values = _values(described, key)
    return values[0] if values else None


def _attributes_of(entry: Mapping[str, Any]) -> Mapping[str, str]:
    """Return one occurrence's attributes, whatever the projection put there.

    Args:
        entry: One occurrence from a projected description block.

    Returns:
        The attribute mapping, empty when the element carried none.
    """
    attributes = entry.get("attributes")
    return attributes if isinstance(attributes, Mapping) else {}


def _attribute(attributes: Mapping[str, str], name: str) -> str | None:
    """Return an attribute by its local name, whatever prefix the source gave it.

    The prefix is not part of the attribute's meaning here, and Rechtspraak does not use one
    consistently: a European Dutch judgment writes the court's vocabulary URI as
    ``resourceIdentifier`` in the ``overheid.RechterlijkeMacht`` scheme, while a judgment of a
    Caribbean court of the Kingdom writes the same thing as ``psi:resourceIdentifier`` in the
    ``psi.rechtspraak`` scheme (verified live on 5 August 2026 against ``ECLI:NL:OGEAM:2025:155``
    and ``ECLI:NL:OGHACMB:2025:1``). Matching the qualified name would silently miss the second
    form, and missing it costs the court's identity — see :meth:`RechtspraakConnector._court`.

    Args:
        attributes: One occurrence's attributes, qualified.
        name: The local attribute name, e.g. ``resourceIdentifier``.

    Returns:
        The first matching value, or ``None``.
    """
    for qualified, value in attributes.items():
        if qualified.rpartition(":")[2] == name:
            return str(value)
    return None


def _first_attribute(described: Described, key: str, attribute: str) -> str | None:
    """Return one attribute of the first occurrence of an element that carries it.

    Args:
        described: A projected description block.
        key: Qualified element name.
        attribute: Local attribute name; see :func:`_attribute` on why the prefix is ignored.

    Returns:
        The attribute value, or ``None`` when either the element or the attribute is absent.
    """
    for entry in described.get(key, ()):
        value = _attribute(_attributes_of(entry), attribute)
        if value is not None:
            return value
    return None


def _labelled(described: Described, key: str) -> list[dict[str, str | None]]:
    """Lift every occurrence of a controlled value as a label and its vocabulary URI.

    Args:
        described: A projected description block.
        key: Qualified element name of a repeatable, controlled element such as
            ``dcterms:subject`` or ``psi:procedure``.

    Returns:
        One entry per occurrence, in document order. A list even when the document carries a
        single value, because the element may repeat: a case classified under two
        *rechtsgebieden*, or heard under both an ``eersteAanlegMeervoudig`` procedure and a
        ``proceskostenveroordeling``, must not lose the second one.
    """
    return [
        {
            "label": str(entry["value"]) if entry.get("value") is not None else None,
            "uri": _attribute(_attributes_of(entry), "resourceIdentifier"),
        }
        for entry in described.get(key, ())
    ]


def _parse_date(value: str | None) -> date | None:
    """Read an ISO 8601 date from the metadata block.

    Args:
        value: The raw value, or ``None``.

    Returns:
        The date, or ``None`` when the value is missing or is not a date. A source that
        publishes an unreadable date must not cost the whole document.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        log.debug("unreadable date in the metadata block", extra={"context": {"value": value}})
        return None


def _parse_instant(value: str | None) -> datetime | None:
    """Read an Atom timestamp as a timezone-aware UTC instant.

    Args:
        value: The raw value, or ``None``.

    Returns:
        The instant in UTC, or ``None`` when the value is missing or unreadable. A value
        without an offset is read as UTC, which is what this feed always sends.
    """
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        log.debug("unreadable timestamp in the feed", extra={"context": {"value": value}})
        return None
    return moment.astimezone(UTC) if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _source_timezone() -> ZoneInfo | None:
    """Return the timezone the ``modified`` parameter is read in, if it can be resolved.

    Returns:
        The Europe/Amsterdam zone, or ``None`` when no timezone database is installed — a
        Windows process without ``tzdata``. The caller widens the window instead, which costs
        a few extra feed pages and no correctness, since every entry is checked against the
        caller's own UTC bounds regardless.
    """
    try:
        return ZoneInfo(_SOURCE_TIMEZONE)
    except (ZoneInfoNotFoundError, KeyError):  # pragma: no cover - platform dependent
        log.warning(
            "no timezone database available; widening the discovery window instead",
            extra={"context": {"timezone": _SOURCE_TIMEZONE}},
        )
        return None


def _window_bound(moment: datetime) -> str:
    """Render one end of a window in the local time the endpoint expects.

    Args:
        moment: The instant, timezone-aware.

    Returns:
        A ``YYYY-MM-DDTHH:MM:SS`` string in Europe/Amsterdam local time. Without a timezone
        database the UTC clock value is sent instead, which asks the endpoint for a window
        shifted one or two hours early; :meth:`RechtspraakConnector.discover` compensates by
        extending the *whole walk* by :data:`_CLOCK_MARGIN` rather than by widening each
        window, which would make consecutive windows overlap by two hours each.
    """
    zone = _source_timezone()
    return (moment.astimezone(zone) if zone is not None else moment.astimezone(UTC)).strftime(
        _WINDOW_FORMAT
    )


def _extract_text(element: etree._Element) -> str:
    """Extract the readable plain text of a Rechtspraak document body.

    Walked with an explicit stack rather than by recursion, so a deeply nested document
    cannot exhaust the interpreter's stack, and text is emitted in document order with a line
    break after each block-level element. Processing instructions are honoured too: the
    schema's ``<?linebreak?>`` carries the sentence that follows it as its tail, and skipping
    it would silently lose text the filter has to read.

    Args:
        element: The ``uitspraak``, ``conclusie`` or ``inhoudsindicatie`` element.

    Returns:
        The element's text, with runs of blank lines collapsed and the ends stripped.
    """
    parts: list[str] = []
    stack: list[tuple[etree._Element, bool]] = [(element, False)]
    while stack:
        node, closing = stack.pop()
        if closing:
            if _local_name(node) in _BLOCK_ELEMENTS:
                parts.append("\n")
            if node is not element and node.tail:
                parts.append(node.tail)
            continue
        if _is_named(node):
            if node.text:
                parts.append(node.text)
            stack.append((node, True))
            stack.extend((child, False) for child in reversed(node))
            continue
        # A comment or a processing instruction. It has no text of its own, but its tail is
        # ordinary document text and the schema's line-break instruction is meaningful.
        if isinstance(node, etree._ProcessingInstruction) and node.target == _LINEBREAK:
            parts.append("\n")
        if node.tail:
            parts.append(node.tail)
    joined = "".join(parts).replace("\r\n", "\n").replace("\r", "\n")
    return _BLANK_RUN.sub("\n\n", _LINE_PADDING.sub("\n", joined)).strip()


class CourtRecord:
    """One court of the ``Waardelijst/Instanties`` vocabulary.

    Attributes:
        identifier: The vocabulary URI, used as ``court.source_identifier``.
        name: The court's name.
        abbreviation: The code that appears in an ECLI, e.g. ``RBDHA``.
        court_type: The vocabulary's own type, e.g. ``Rechtbank``.
        begin_date: First day the court existed, as the vocabulary states it.
        end_date: Last day it existed, or ``None`` for a court still sitting.
    """

    __slots__ = ("abbreviation", "begin_date", "court_type", "end_date", "identifier", "name")

    def __init__(
        self,
        identifier: str,
        name: str,
        *,
        abbreviation: str | None = None,
        court_type: str | None = None,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> None:
        """Record one vocabulary entry.

        Args:
            identifier: The vocabulary URI.
            name: The court's name.
            abbreviation: The ECLI court code.
            court_type: The vocabulary's own type.
            begin_date: First day the court existed.
            end_date: Last day it existed.
        """
        self.identifier = identifier
        self.name = name
        self.abbreviation = abbreviation
        self.court_type = court_type
        self.begin_date = begin_date
        self.end_date = end_date

    @property
    def level(self) -> str | None:
        """Return the court's instance in the judicial hierarchy."""
        return _COURT_TYPES.get(self.court_type or "", (None, None))[0]

    @property
    def domain(self) -> str | None:
        """Return the court's subject-matter domain, where the vocabulary implies one."""
        return _COURT_TYPES.get(self.court_type or "", (None, None))[1]

    def normalised(self) -> NormalisedCourt:
        """Return this entry as the value the pipeline resolves against the ``court`` table.

        Everything the vocabulary states about the court is carried: the normalised
        :attr:`level` and :attr:`domain` the API filters on, the raw ``Type`` behind them, and
        the dates between which the court sat, which have no column and go to the row's
        ``source_metadata``. The entry is read once per run, so a field dropped here is a
        field only another request can recover.

        Returns:
            The court, identified by its vocabulary URI rather than by its name, so a renamed
            court updates its row instead of becoming a second one.
        """
        return NormalisedCourt(
            source_identifier=self.identifier,
            name=self.name,
            level=self.level,
            domain=self.domain,
            source_type=self.court_type,
            abbreviation=self.abbreviation,
            source_url=self.identifier,
            source_metadata={
                "type": self.court_type,
                "begin_date": self.begin_date,
                "end_date": self.end_date,
            },
        )

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs and test failures."""
        return f"CourtRecord(identifier={self.identifier!r}, name={self.name!r})"


class RechtspraakConnector(SourceConnector):
    """The Netherlands, through the Rechtspraak.nl open data API.

    Attributes:
        jurisdiction_code: ``NL``.
        name: ``rechtspraak``; written to ``ingest_run.connector`` and the primary key of
            ``ingest_checkpoint``.
    """

    jurisdiction_code: ClassVar[str] = "NL"
    name: ClassVar[str] = "rechtspraak"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: PoliteClient | None = None,
    ) -> None:
        """Bind the connector to its configuration.

        Args:
            settings: Settings supplying the endpoints, the page size, the request rate and
                the ``User-Agent``. Defaults to the process-wide settings.
            client: HTTP client to fetch through. Defaults to a
                :class:`~plt.pipeline.http.PoliteClient` built from the settings, which is
                what applies the request rate, the backoff and the descriptive
                ``User-Agent``. Injected by the tests, which serve recorded fixtures through
                an :class:`httpx.MockTransport` and never touch the network.
        """
        super().__init__(settings)
        self._client = client if client is not None else PoliteClient(self.settings)
        self._owns_client = client is None
        self._courts: dict[str, CourtRecord] | None = None

    @property
    def traffic(self) -> SourceTraffic:
        """Return what this connector has asked of Rechtspraak.nl so far."""
        return self._client.traffic

    # -- Discovery --------------------------------------------------------------------

    def discover(self, since: datetime | None, until: datetime | None) -> Iterator[Candidate]:
        """Yield every document modified in the window, oldest first, one window at a time.

        **The walk does not page.** The range is cut into windows narrow enough that the feed
        answers each of them in a single request, because paging this feed means trusting an
        order it does not offer: ``sort=ASC`` sorts on ``updated``, which is not unique, and
        where two entries share a timestamp their relative order is the endpoint's to choose.
        Choose differently on either side of a page boundary and one entry comes back twice
        while another never comes back at all — the defect that cost the EU corpus a sixth of
        its cases, reported as ``success`` with zero failures throughout (issue #99). CELLAR
        could be re-paged by CELEX number; this feed offers ``from`` and ``max`` and nothing
        to page *after*, so there is no key to page by and the only cure is not to page.

        The window a walk asks for is not the window it is given. The feed states its own
        total for any window in ``atom:subtitle``, so each window is measured before it is
        trusted: one that holds more than a page is halved and measured again until it fits,
        and one that holds almost nothing widens the next, which is how a backfill crosses
        the empty century before 2013 in a few dozen requests instead of forty thousand. Two
        entries that share a timestamp always land in the same window, because a window
        boundary is a whole second and so is a tie — which is what makes "does not page" a
        property of the arithmetic rather than a hope about the endpoint.

        One second holding more than a page is the single case this cannot solve; it is paged,
        and it is logged, because a window nobody can enumerate exactly must not be
        indistinguishable from one that came back whole.

        Entries arrive in ascending order of the modification timestamp the checkpoint
        records, within a window and across windows, which is what makes an interrupted run
        resume from the right place. Memory is one window's entries — at most
        ``rechtspraak_page_size`` of them, and the setting is capped at the endpoint's own
        maximum of 1000 (``docs/architecture.md`` rule 2.3).

        Args:
            since: Only documents modified at or after this instant, inclusive. ``None`` walks
                from :data:`_EARLIEST`.
            until: Only documents modified at or before this instant, inclusive, or ``None``
                for "up to now".

        Yields:
            One :class:`~plt.pipeline.base.Candidate` per document, carrying the ECLI, the
            modification instant, the Atom revision marker and the portal URL.

        Raises:
            SourceUnavailableError: If the feed cannot be read at all.
        """
        start = since if since is not None else _EARLIEST
        stop = until if until is not None else datetime.now(UTC)
        if _source_timezone() is None:
            # Every bound is being sent one or two hours early. Extending the end of the walk
            # is what brings the far edge back into range; the near edge is already covered,
            # and both are trimmed exactly against each entry's own UTC timestamp below.
            stop = stop + _CLOCK_MARGIN
        if start > stop:
            log.info(
                "empty discovery window",
                extra={"context": {"since": start.isoformat(), "until": stop.isoformat()}},
            )
            return
        for window, entries in self._windows(start, stop):
            for position, entry in enumerate(entries):
                candidate = self._candidate(entry, window, position)
                if candidate is None:
                    continue
                moment = candidate.modified_at
                if moment is not None:
                    if since is not None and moment < since:
                        continue
                    if until is not None and moment > until:
                        continue
                yield candidate

    def _windows(self, start: datetime, stop: datetime) -> Iterator[tuple[Window, list[Entry]]]:
        """Yield each window of the walk with the entries it holds, oldest first.

        The windows are **inclusive of both ends**, which is how the endpoint reads a pair of
        ``modified`` values, and the next one starts one second after the last one stopped.
        Because a bound resolves to the second, that covers the range exactly: no instant the
        endpoint can express falls between two windows, and none falls in both.

        The width is not configuration so much as a starting guess. It doubles while windows
        come back under a quarter full and halves while they come back over three-quarters
        full, so the walk settles near one request per window over dense years and crosses
        sparse ones in strides. ``rechtspraak_window_days`` only says where it starts looking.

        Args:
            start: Inclusive lower bound of the walk.
            stop: Inclusive upper bound.

        Yields:
            Each window and its entries, covering the range without gaps or overlap.

        Raises:
            SourceUnavailableError: If the feed cannot be read at all.
        """
        page_size = max(1, min(self.settings.rechtspraak_page_size, _MAX_PAGE_SIZE))
        floor = timedelta(seconds=self.settings.rechtspraak_min_window_seconds)
        ceiling = max(timedelta(days=self.settings.rechtspraak_max_window_days), floor)
        width = min(max(timedelta(days=self.settings.rechtspraak_window_days), floor), ceiling)
        log.info(
            "discovering Dutch case law",
            extra={
                "context": {
                    "connector": self.name,
                    "since": start.isoformat(),
                    "until": stop.isoformat(),
                    "page_size": page_size,
                    "window_days": width.total_seconds() / 86400,
                }
            },
        )
        cursor = start
        while cursor <= stop:
            window = Window(cursor, min(cursor + width, stop))
            total, entries = self._search(window, page_size)
            # Short of what the feed counted, rather than short of the page: the two are the
            # same thing whenever the endpoint is behaving, and where they differ it is the
            # count that says whether anything was left behind.
            if total is None or total > len(entries):
                window, total, entries = self._fit(window, total, entries, page_size, floor)
            yield window, entries
            width = self._next_width(max(window.width, floor), total, page_size, floor, ceiling)
            cursor = window.stop + _TICK

    def _fit(
        self,
        window: Window,
        total: int | None,
        entries: list[Entry],
        page_size: int,
        floor: timedelta,
    ) -> tuple[Window, int | None, list[Entry]]:
        """Narrow a window that did not fit in one request until it does, and read it.

        Args:
            window: The window that overflowed.
            total: What the feed said the window holds, or ``None`` when it said nothing a
                number could be read out of — in which case the window cannot be measured and
                so cannot be narrowed to fit, and is paged instead.
            entries: What the first read of the window returned, which is the answer already
                when there turns out to be nothing to narrow.
            page_size: Entries one request returns.
            floor: Narrowest the window may become.

        Returns:
            The window actually read, the total the feed stated for it, and its entries.

        Raises:
            SourceUnavailableError: If the feed cannot be read at all.
        """
        if total is not None and total <= page_size:
            # Fewer entries than the feed's own count, in a window it says fits in one
            # request. Nothing here can fix that, and asking again would only ask the same
            # question — but a corpus short of what its source counted should say so.
            log.warning(
                "the feed answered a window with fewer entries than it counted; the walk "
                "returns what arrived",
                extra={
                    "context": {
                        "connector": self.name,
                        "window": window.cursor(0),
                        "counted": total,
                        "returned": len(entries),
                    }
                },
            )
            return window, total, entries
        while total is not None and total > page_size and window.width > floor:
            window = window.halved(floor)
            total = self._count(window)
        if total is None:
            log.warning(
                "the feed stated no total for a window, so it cannot be measured before it is "
                "read; paging it instead, over an order the endpoint does not guarantee",
                extra={"context": {"connector": self.name, "window": window.cursor(0)}},
            )
            return window, None, self._paged(window, page_size, None)
        if total > page_size:
            log.warning(
                "a single second holds more entries than one request returns, so this window "
                "has to be paged over a sort that is not a total order; entries sharing an "
                "updated timestamp may be returned twice or not at all. Raise "
                "PLT_RECHTSPRAAK_PAGE_SIZE if it is below the endpoint maximum of "
                f"{_MAX_PAGE_SIZE}",
                extra={
                    "context": {
                        "connector": self.name,
                        "window": window.cursor(0),
                        "entries": total,
                        "page_size": page_size,
                    }
                },
            )
            return window, total, self._paged(window, page_size, total)
        _, entries = self._search(window, page_size)
        return window, total, entries

    @staticmethod
    def _next_width(
        width: timedelta,
        total: int | None,
        page_size: int,
        floor: timedelta,
        ceiling: timedelta,
    ) -> timedelta:
        """Return the width to try for the next window.

        Args:
            width: Width of the window just read.
            total: How many entries it held, or ``None`` when the feed did not say.
            page_size: Entries one request returns.
            floor: Narrowest a window may become.
            ceiling: Widest a window may become.

        Returns:
            The next width: doubled while windows come back nearly empty, halved while they
            come back nearly full, unchanged in between. An unmeasurable window leaves the
            width alone, because there is nothing to conclude from it.
        """
        if total is None:
            return width
        if total <= page_size * _GROW_BELOW:
            return min(width * 2, ceiling)
        if total >= page_size * _SHRINK_ABOVE:
            return max(width / 2, floor)
        return width

    def _count(self, window: Window) -> int | None:
        """Return the number of entries the feed says a window holds.

        Asked with ``max=1``, so measuring a window that is about to be discarded costs one
        entry rather than a page.

        Args:
            window: The window to measure.

        Returns:
            The stated total, or ``None`` when the feed stated none.

        Raises:
            SourceUnavailableError: If the feed cannot be read at all.
        """
        total, _ = self._search(window, 1)
        return total

    def _paged(self, window: Window, page_size: int, total: int | None) -> list[Entry]:
        """Read a window that could not be narrowed to fit, by offset.

        The one place in this connector that still depends on the endpoint holding an order
        steady across requests. It is reached only for a window already at
        ``rechtspraak_min_window_seconds``, and :meth:`_fit` logs before calling it.

        Args:
            window: The window to read.
            page_size: Entries one request returns.
            total: How many entries the feed says the window holds, or ``None`` to read on
                until a short page ends it.

        Returns:
            Every entry the pages held, in the order they arrived.

        Raises:
            SourceUnavailableError: If the feed cannot be read at all.
        """
        entries: list[Entry] = []
        offset = 0
        while True:
            _, page = self._search(window, page_size, offset)
            if not page:
                return entries
            entries.extend(page)
            offset += len(page)
            if len(page) < page_size or (total is not None and offset >= total):
                return entries

    def _search(
        self, window: Window, page_size: int, offset: int = 0
    ) -> tuple[int | None, list[Entry]]:
        """Fetch and parse one search request.

        Args:
            window: The window to ask for, both ends inclusive.
            page_size: Number of entries to request.
            offset: Zero-based offset of the first entry.

        Returns:
            The total the feed states for the window and one mapping per Atom entry, holding
            its identifier, title, summary, modification timestamp and alternate link. Entries
            are released as they are read, so a request costs its entries rather than its
            parse tree.

        Raises:
            SourceUnavailableError: If the feed cannot be read or is not well-formed. A broken
                index is a broken source, not one broken document.
        """
        params: list[tuple[str, str]] = [
            ("max", str(page_size)),
            ("from", str(offset)),
            ("sort", "ASC"),
        ]
        if self.settings.rechtspraak_documents_only:
            params.append(("return", "DOC"))
        params.append(("modified", _window_bound(window.start)))
        params.append(("modified", _window_bound(window.stop)))
        response = self._client.get(
            self.settings.rechtspraak_search_url, params=httpx.QueryParams(tuple(params))
        )
        try:
            feed = etree.fromstring(_as_bytes(response.content), parser=_parser())
        except etree.XMLSyntaxError as error:
            message = (
                f"the Rechtspraak search feed for {window.cursor(offset)} is not well-formed: "
                f"{error}"
            )
            raise SourceUnavailableError(message) from error
        entries: list[Entry] = []
        for entry in feed.iterfind(f"{{{_ATOM}}}entry"):
            entries.append(
                {
                    "id": entry.findtext(f"{{{_ATOM}}}id"),
                    "title": entry.findtext(f"{{{_ATOM}}}title"),
                    "summary": entry.findtext(f"{{{_ATOM}}}summary"),
                    "updated": entry.findtext(f"{{{_ATOM}}}updated"),
                    "link": self._alternate_link(entry),
                }
            )
            entry.clear()
        subtitle = (feed.findtext(f"{{{_ATOM}}}subtitle") or "").strip()
        stated = _STATED_TOTAL.search(subtitle)
        total = int(stated.group(1)) if stated is not None else None
        log.debug(
            "search request read",
            extra={
                "context": {
                    "connector": self.name,
                    "window": window.cursor(offset),
                    "entries": len(entries),
                    "total": total,
                }
            },
        )
        return total, entries

    @staticmethod
    def _alternate_link(entry: etree._Element) -> str | None:
        """Return the portal URL an Atom entry links to.

        Args:
            entry: The ``atom:entry`` element.

        Returns:
            The ``rel="alternate"`` href, or ``None``.
        """
        for link in entry.iterfind(f"{{{_ATOM}}}link"):
            if link.get("rel", "alternate") == "alternate":
                return link.get("href")
        return None

    def _candidate(self, entry: Entry, window: Window, position: int) -> Candidate | None:
        """Turn one parsed Atom entry into a candidate.

        Args:
            entry: The parsed entry.
            window: The window it was found in, which the cursor names so an interrupted run's
                position says *where* it stopped rather than only how far in.
            position: Its zero-based offset within that window.

        Returns:
            The candidate, or ``None`` when the entry carries no usable identifier — which
            costs one skipped entry rather than the whole window.
        """
        identifier = (entry.get("id") or "").strip()
        if not identifier:
            log.warning(
                "feed entry without an identifier",
                extra={"context": {"at": window.cursor(position)}},
            )
            return None
        summary = (entry.get("summary") or "").strip() or None
        return Candidate(
            source_id=identifier,
            jurisdiction_code=self.jurisdiction_code,
            modified_at=_parse_instant(entry.get("updated")),
            # The Atom ``updated`` is the source's own revision marker, so the pre-fetch check
            # can skip an unchanged document without spending a request on it.
            content_hash=self._revision_hash(identifier, entry.get("updated")),
            cursor=window.cursor(position),
            title=(entry.get("title") or "").strip() or None,
            source_url=(entry.get("link") or "").strip() or None,
            source_metadata={"summary": summary},
        )

    @staticmethod
    def _revision_hash(identifier: str, updated: str | None) -> str | None:
        """Hash a feed entry's revision marker into the pipeline's shared hash space.

        Args:
            identifier: The document's ECLI.
            updated: The Atom ``updated`` value, if the entry carries one.

        Returns:
            A 64-character digest, or ``None`` when the entry states no revision — in which
            case deduplication falls back to hashing the fetched content.
        """
        if not updated or not updated.strip():
            return None
        return content_hash(["rechtspraak", identifier, updated.strip()])

    # -- Fetching ---------------------------------------------------------------------

    def fetch(self, candidate: Candidate) -> RawDocument:
        """Retrieve one document's Rechtspraak XML, verbatim.

        Args:
            candidate: The candidate to retrieve.

        Returns:
            The unmodified response, which is stored as ``case_document.raw_payload``.

        Raises:
            DocumentUnavailableError: If the identifier is not a well-formed ECLI, or the
                endpoint does not publish it.
            SourceUnavailableError: If the endpoint as a whole has become unusable.
        """
        identifier = candidate.source_id.strip()
        if not _ECLI_PATTERN.match(identifier):
            message = f"{identifier!r} is not a well-formed ECLI; not requesting it"
            raise DocumentUnavailableError(message)
        response = self._client.get(
            self.settings.rechtspraak_content_url, params={"id": identifier}
        )
        return RawDocument(
            candidate=candidate,
            payload=response.text,
            media_format="xml",
            source_url=str(response.url),
            source_metadata={
                "content_type": response.headers.get("Content-Type"),
                "etag": response.headers.get("ETag"),
            },
        )

    # -- Normalisation ----------------------------------------------------------------

    def normalise(self, raw: RawDocument) -> NormalisedCase:
        """Map one Rechtspraak XML payload onto the schema, keeping everything else.

        Every element of the Dublin Core block is captured: the ones the schema names reach
        their column, and the rest — the *zittingsplaats*, the *vindplaatsen*, the coverage,
        the publisher, the resource identifier behind each controlled value — reach
        ``case.source_metadata`` under ``dublin_core``, verbatim and unflattened. The response
        itself is kept as the document's ``raw_payload``, so a reclassification never has to
        ask the courts for the same judgment twice.

        Args:
            raw: The payload returned by :meth:`fetch`.

        Returns:
            The normalised case.

        Raises:
            DocumentUnavailableError: If the payload is not well-formed XML or carries no
                metadata block to read.
        """
        root = _parse(raw.payload, described_as="the document", source_id=raw.source_id)
        blocks = root.findall(f"{{{_RDF}}}RDF/{{{_RDF}}}Description")
        if not blocks:
            message = f"the document for {raw.source_id} carries no rdf:Description block"
            raise DocumentUnavailableError(message)
        described = _describe(blocks[0])
        deeplink = _describe(blocks[1]) if len(blocks) > 1 else {}

        subjects = _values(described, "dcterms:subject")
        domain, subfield = self._classify(subjects)
        language = _first_value(described, "dcterms:language")
        documents = self._documents(root, raw, language=language)

        return NormalisedCase(
            source_id=_first_value(described, "dcterms:identifier") or raw.source_id,
            jurisdiction_code=self.jurisdiction_code,
            source_system=self.name,
            title=_first_value(deeplink, "dcterms:title") or raw.candidate.title,
            abstract=self._abstract(root),
            subject=_SUBJECT_SEPARATOR.join(subjects) or None,
            decision_date=_parse_date(_first_value(described, "dcterms:date")),
            publication_date=_parse_date(_first_value(described, "dcterms:issued")),
            case_numbers=tuple(_values(described, "psi:zaaknummer")),
            language=language,
            law_domain=domain,
            law_subfield=subfield,
            procedure_type=_first_value(described, "psi:procedure"),
            source_url=raw.candidate.source_url or self._deeplink(deeplink, blocks),
            court=self._court(described),
            documents=documents,
            citations=self._citations(described),
            source_metadata=self._metadata(described, deeplink, blocks, raw, documents=documents),
        )

    @staticmethod
    def _classify(subjects: Sequence[str]) -> tuple[LawDomain | None, str | None]:
        """Split the hierarchical *rechtsgebied* into a law domain and a subfield.

        The vocabulary is written ``Bestuursrecht; Vreemdelingenrecht`` — the root and the
        branch in one value — so both schema columns come out of it without a lookup table of
        court names and without a second request.

        Args:
            subjects: The classification labels, in document order.

        Returns:
            The domain and the subfield. The first recognised label decides, because the
            schema holds one of each; every label stays in ``subject`` and in
            ``source_metadata``, so nothing is lost by choosing here.
        """
        for label in subjects:
            root, _, branch = label.partition(";")
            domain = _LAW_DOMAINS.get(root.strip().casefold())
            if domain is None:
                continue
            return domain, branch.strip() or None
        return (LawDomain.OTHER if subjects else None), None

    @staticmethod
    def _abstract(root: etree._Element) -> str | None:
        """Return the ``inhoudsindicatie``, the editor's summary of the decision.

        Args:
            root: The document root.

        Returns:
            The summary text, or ``None`` when the document carries none. It is a scored field
            of the keyword filter and, for a document published without a body, often the only
            text there is.
        """
        summary = root.find(f"{{{_RECHTSPRAAK}}}inhoudsindicatie")
        if summary is None:
            return None
        return _extract_text(summary) or None

    def _documents(
        self,
        root: etree._Element,
        raw: RawDocument,
        *,
        language: str | None,
    ) -> tuple[NormalisedDocument, ...]:
        """Build the ``case_document`` rows for one payload.

        A document with a body yields one row of the matching kind carrying its extracted
        text; a document without one still yields a row, so the verbatim XML is stored either
        way, but with no ``full_text`` — which is what keeps a metadata-only ECLI out of the
        filter's way instead of handing it an empty string to score.

        The ``inhoudsindicatie`` deliberately does not become a second row. It has a column of
        its own on ``case.abstract`` and the keyword filter scores it there at its own
        multiplier; repeating it here would put the same sentence into ``full_text`` as well
        and score one summary twice.

        Args:
            root: The document root.
            raw: The payload the row's ``raw_payload`` is taken from.
            language: The document's language, from the metadata block.

        Returns:
            Exactly one document.
        """
        for element in root:
            kind = _BODY_ELEMENTS.get(_local_name(element))
            if kind is None:
                continue
            return (
                NormalisedDocument(
                    doc_type=kind,
                    language=element.get("lang") or language,
                    full_text=_extract_text(element) or None,
                    raw_payload=raw.payload,
                    media_format=raw.media_format,
                    source_url=raw.candidate.source_url,
                    source_metadata={"element": _local_name(element)},
                ),
            )
        log.debug(
            "document published without a body",
            extra={"context": {"connector": self.name, "source_id": raw.source_id}},
        )
        return (
            NormalisedDocument(
                doc_type=DocumentType.OTHER,
                language=language,
                full_text=None,
                raw_payload=raw.payload,
                media_format=raw.media_format,
                source_url=raw.candidate.source_url,
                source_metadata={"element": None},
            ),
        )

    @staticmethod
    def _deeplink(deeplink: Described, blocks: Sequence[etree._Element]) -> str | None:
        """Return the canonical deeplink of the decision.

        Args:
            deeplink: The projected second ``rdf:Description``, which describes the HTML
                rendering of the decision.
            blocks: The description blocks, whose ``rdf:about`` carries the same URL.

        Returns:
            The deeplink URL, or ``None``.
        """
        recorded = _first_value(deeplink, "dcterms:identifier")
        if recorded:
            return recorded
        if len(blocks) > 1:
            return blocks[1].get(f"{{{_RDF}}}about")
        return None

    def _court(self, described: Described) -> NormalisedCourt | None:
        """Resolve the deciding court against the ``Instanties`` vocabulary.

        The vocabulary's URI is the identity — ``court.source_identifier`` — and the level,
        domain, raw type and abbreviation come from the vocabulary too, rather than from a
        table of court names in this file. Supplying them all on every case matters:
        persistence writes them onto the court row each time, so a connector that left them
        ``None`` would erase what ``plt seed-vocabularies`` had put there. A court the
        vocabulary does not list — or any court, when the vocabulary could not be read at all
        — is stored with its name alone, which is the one case where the row carries no type.

        **The identifying attribute is read by its local name on purpose.** The portal
        qualifies it for the Caribbean courts of the Kingdom and not for the European Dutch
        ones (:func:`_attribute`). Reading only the unqualified form left every Kingdom
        judgment falling through to the name-derived key below, which matches nothing in the
        vocabulary: those cases lost their court's level, domain and type, and pointed at a
        court row of their own beside the one seeding had already created for the same court.

        Args:
            described: The projected metadata block.

        Returns:
            The court, or ``None`` when the document names none.
        """
        name = _first_value(described, "dcterms:creator")
        identifier = _first_attribute(described, "dcterms:creator", "resourceIdentifier")
        if not name and not identifier:
            return None
        key = identifier or f"{_PSI}instantie/{name}"
        known = self._vocabulary().get(key)
        if known is not None:
            return known.normalised()
        return NormalisedCourt(source_identifier=key, name=name or key, source_url=identifier)

    @staticmethod
    def _citations(described: Described) -> tuple[NormalisedCitation, ...]:
        """Build the citation rows from the statute and case references.

        ``dcterms:references`` carries a statute — a BWB identifier, occasionally a CELEX
        number for a Union instrument — and ``dcterms:relation`` carries a related decision by
        ECLI: the conclusie behind an arrest, the earlier instance behind an appeal. Both
        become ``citation`` rows, with the scheme read off the prefix of the identifying
        attribute rather than guessed from the value.

        Args:
            described: The projected metadata block.

        Returns:
            The citations, in document order.
        """
        citations: list[NormalisedCitation] = []
        relations = (("dcterms:references", "cites"), ("dcterms:relation", "related"))
        for key, citation_type in relations:
            for entry in described.get(key, ()):
                attributes = _attributes_of(entry)
                target, scheme = _identify(attributes)
                if target is None or scheme is None:
                    continue
                citations.append(
                    NormalisedCitation(
                        target_identifier=target,
                        citation_type=citation_type,
                        target_scheme=scheme,
                        target_title=str(entry.get("value") or "") or None,
                        source_metadata={
                            name: value
                            for name, value in attributes.items()
                            if not name.endswith(":resourceIdentifier")
                        },
                    )
                )
        return tuple(citations)

    def _metadata(
        self,
        described: Described,
        deeplink: Described,
        blocks: Sequence[etree._Element],
        raw: RawDocument,
        *,
        documents: Sequence[NormalisedDocument],
    ) -> dict[str, Any]:
        """Assemble everything the endpoint exposed that the schema has no column for.

        Args:
            described: The projected metadata block.
            deeplink: The projected description of the HTML rendering.
            blocks: The description blocks, for the deeplink URL.
            raw: The payload, for the response metadata worth keeping.
            documents: The documents built for this case.

        Returns:
            The mapping stored as ``case.source_metadata``. ``dublin_core`` is the complete
            block, verbatim and unflattened; the named keys beside it are the values a report
            or a later classifier reads most often, lifted out so nothing has to walk the
            block to find them.

            **A lifted key has the cardinality of the element it comes from.** Every element
            the Rechtspraak schema allows to repeat is lifted as a list, even when a
            particular document happens to carry one — ``psi:zaaknummer``,
            ``dcterms:subject``, ``psi:procedure``, ``dcterms:spatial`` and the *vindplaatsen*.
            Anything singular in the schema is lifted as a scalar. A reader can therefore tell
            from the key alone whether a second value is possible, instead of discovering on
            some later document that a field it treated as one value is actually two.
        """
        return {
            "dublin_core": {name: list(entries) for name, entries in described.items()},
            "deeplink_description": {name: list(entries) for name, entries in deeplink.items()},
            "deeplink": self._deeplink(deeplink, blocks),
            "modified": _first_value(described, "dcterms:modified"),
            "coverage": _first_value(described, "dcterms:coverage"),
            "zittingsplaatsen": _values(described, "dcterms:spatial"),
            "zaaknummers": _values(described, "psi:zaaknummer"),
            "rechtsgebieden": _labelled(described, "dcterms:subject"),
            "procedures": _labelled(described, "psi:procedure"),
            "vindplaatsen": [
                item
                for entry in described.get("dcterms:hasVersion", ())
                for item in entry.get("items", ())
            ],
            "instantie": {
                "name": _first_value(described, "dcterms:creator"),
                "uri": _first_attribute(described, "dcterms:creator", "resourceIdentifier"),
                "scheme": _first_attribute(described, "dcterms:creator", "scheme"),
            },
            "document_type": {
                "label": _first_value(described, "dcterms:type"),
                "uri": _first_attribute(described, "dcterms:type", "resourceIdentifier"),
            },
            "publisher": {
                "name": _first_value(described, "dcterms:publisher"),
                "uri": _first_attribute(described, "dcterms:publisher", "resourceIdentifier"),
            },
            "access_rights": _first_value(described, "dcterms:accessRights"),
            "format": _first_value(described, "dcterms:format"),
            "has_body": any(document.has_text for document in documents),
            "response": dict(raw.source_metadata),
        }

    # -- Controlled vocabulary --------------------------------------------------------

    def iter_courts(self) -> Iterator[NormalisedCourt]:
        """Yield every court of the ``Instanties`` vocabulary, for reference-table seeding.

        Yields:
            One :class:`~plt.pipeline.base.NormalisedCourt` per vocabulary entry, identified
            by its URI, so seeding is idempotent however often it runs.

        Raises:
            SourceUnavailableError: If the vocabulary cannot be read.
        """
        for record in self._vocabulary(required=True).values():
            yield record.normalised()

    def _vocabulary(self, *, required: bool = False) -> Mapping[str, CourtRecord]:
        """Return the court vocabulary, fetching it at most once per connector.

        Args:
            required: Whether a failure to read it should end the caller. Seeding says yes —
                an empty vocabulary is a failed command, not an empty table. Normalisation
                says no: a court whose level is momentarily unknown is a far better outcome
                than a run that stops.

        Returns:
            The vocabulary, keyed on each entry's URI.

        Raises:
            SourceUnavailableError: If the vocabulary cannot be read and ``required`` is set.
        """
        if self._courts is not None:
            return self._courts
        url = f"{self.settings.rechtspraak_vocabulary_url.rstrip('/')}/Instanties"
        try:
            response = self._client.get(url)
            root = etree.fromstring(_as_bytes(response.content), parser=_parser())
        except (SourceUnavailableError, DocumentUnavailableError, etree.XMLSyntaxError) as error:
            if required:
                message = f"the court vocabulary at {url} could not be read: {error}"
                raise SourceUnavailableError(message) from error
            log.warning(
                "court vocabulary unavailable; courts are stored with their name only",
                extra={"context": {"url": url, "error": f"{type(error).__name__}: {error}"}},
            )
            self._courts = {}
            return self._courts

        records: dict[str, CourtRecord] = {}
        for entry in root.iterfind("Instantie"):
            identifier = (entry.findtext("Identifier") or "").strip()
            if not identifier:
                continue
            records[identifier] = CourtRecord(
                identifier=identifier,
                name=(entry.findtext("Naam") or identifier).strip(),
                abbreviation=(entry.findtext("Afkorting") or "").strip() or None,
                court_type=(entry.findtext("Type") or "").strip() or None,
                begin_date=(entry.findtext("BeginDate") or "").strip() or None,
                end_date=(entry.findtext("EndDate") or "").strip() or None,
            )
        log.info(
            "court vocabulary loaded",
            extra={"context": {"connector": self.name, "courts": len(records)}},
        )
        self._courts = records
        return records

    # -- Lifecycle --------------------------------------------------------------------

    def close(self) -> None:
        """Close the HTTP client, unless the caller passed one in."""
        if self._owns_client:
            self._client.close()


def _identify(attributes: Mapping[str, str]) -> tuple[str | None, str | None]:
    """Read the target identifier and its scheme off a reference element's attributes.

    Args:
        attributes: The element's attributes, qualified.

    Returns:
        The identifier and the scheme it belongs to, or ``(None, None)`` when the element
        carries no identifier this connector recognises.
    """
    for name, value in attributes.items():
        prefix, _, local = name.partition(":")
        scheme = _CITATION_SCHEMES.get(prefix)
        if local == "resourceIdentifier" and scheme is not None and value:
            return str(value), scheme
    return None, None
