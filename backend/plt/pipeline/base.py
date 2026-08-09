"""Connector interface and the data classes that cross pipeline stages.

This module is the contract a jurisdiction is onboarded through. Adding a jurisdiction means
writing one :class:`SourceConnector` subclass and one keyword list in ``data/keywords/`` —
nothing in the runner, the deduplication logic or the persistence layer may learn about a
jurisdiction (``docs/architecture.md`` section 4). Everything jurisdiction-specific therefore
has to be expressible in the three methods below and in the dataclasses they exchange.

The stage order the runner drives is::

    discover -> dedup pre-check -> fetch -> normalise -> filter chain -> persist -> checkpoint

which is why the three methods are separate: ``discover`` must be cheap enough to run over a
whole window, so that :meth:`SourceConnector.fetch` is never called for a document the
database already holds unchanged (``docs/core-document.md`` section 2.6).

Two properties of :class:`NormalisedCase` are worth reading before writing a connector:

* **It satisfies the filter chain's ``FilterableDocument`` protocol.** The schema stores full
  texts in ``case_document``, one row per language, so a case may carry several; the protocol
  wants one ``full_text``. :attr:`NormalisedCase.full_text` resolves that by concatenating the
  text-bearing documents, the case's own language first. A term in any language version
  therefore qualifies the case, which is the behaviour a multilingual jurisdiction needs.
* **``subject`` is a scored field.** For the Netherlands it is the *rechtsgebied*, for the EU
  the subject-matter classification of the notice. Both shipped keyword lists weight it, so a
  connector that leaves it ``None`` throws away a strong signal.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from types import TracebackType
from typing import Any, ClassVar, Self

from plt.config import Settings, get_settings
from plt.db.base import utcnow
from plt.db.models import DocumentType, LawDomain, PartyRole

__all__ = [
    "Candidate",
    "ConnectorError",
    "ConnectorNotFoundError",
    "DocumentUnavailableError",
    "IdentifierListUnavailableError",
    "NormalisedCase",
    "NormalisedCitation",
    "NormalisedCourt",
    "NormalisedDocument",
    "NormalisedParty",
    "PipelineError",
    "RawDocument",
    "SourceConnector",
    "SourceTraffic",
    "SourceUnavailableError",
]

#: Separator between the language versions joined into :attr:`NormalisedCase.full_text`.
#: A blank line, so a term can never be matched across the seam of two documents.
_TEXT_SEPARATOR = "\n\n"


class PipelineError(Exception):
    """Base class for every failure raised by the ingestion pipeline."""


class ConnectorError(PipelineError):
    """Base class for failures originating in a source connector."""


class DocumentUnavailableError(ConnectorError):
    """One document could not be retrieved or parsed.

    The runner logs it, counts it as an error and moves on to the next candidate: a single
    malformed judgment must never abort a weekly run. It does hold the checkpoint back, so
    the document is retried on the next run rather than silently lost.
    """


class SourceUnavailableError(ConnectorError):
    """The source itself is unusable — down, rate-limiting past the retry budget, or broken.

    Fatal to the run. The runner records the failure and leaves the checkpoint untouched, so
    the same window is attempted again next time (``docs/architecture.md`` section 7).
    """


class IdentifierListUnavailableError(ConnectorError):
    """The source cannot list its identifiers more cheaply than by walking discovery.

    Raised by the default :meth:`SourceConnector.enumerate_identifiers`. A repair of a partial
    corpus rests on that listing being cheap (``docs/architecture.md`` rule 2.10); a connector
    that cannot supply one is refused up front rather than quietly given the expensive walk it
    was written to avoid.
    """


class ConnectorNotFoundError(PipelineError):
    """No connector is registered for a jurisdiction.

    Raised by :mod:`plt.pipeline.registry`; a jurisdiction cannot be ingested before its
    connector module exists.
    """


def _require_aware(value: datetime | None, *, field_name: str) -> datetime | None:
    """Reject a naive timestamp before it reaches a timezone-aware column.

    Args:
        value: The timestamp to check, or ``None``.
        field_name: Name of the field, used in the error message.

    Returns:
        The timestamp unchanged.

    Raises:
        ValueError: If the timestamp carries no timezone. ``plt.db.base.UtcDateTime``
            rejects naive values on write; failing here names the connector field instead of
            surfacing as an opaque database error several stages later.
    """
    if value is not None and value.tzinfo is None:
        message = f"{field_name} must be timezone-aware, got the naive value {value!r}"
        raise ValueError(message)
    return value


@dataclass(frozen=True, slots=True)
class Candidate:
    """A document a connector's discovery step found, before it has been fetched.

    Discovery is expected to be cheap — a search feed, a SPARQL result page — and to carry
    just enough to decide whether the document is worth fetching at all. That decision is
    what :attr:`content_hash` exists for.

    Attributes:
        source_id: ECLI (NL) or CELEX (EU); the deduplication key together with the
            jurisdiction.
        jurisdiction_code: Jurisdiction the document belongs to, ``NL`` or ``EU``.
        modified_at: Source modification timestamp, timezone-aware. This is what advances
            the checkpoint, so a connector that can supply it makes its runs incremental.
        content_hash: The source's own revision fingerprint, if discovery exposes one
            cheaply (an Atom ``updated``, a CELLAR modification date). When it is present and
            equals the stored hash, the document is skipped **without being fetched**. A
            connector that supplies it here must produce the identical value for an unchanged
            document at :meth:`SourceConnector.normalise` time, or every run would look like
            a revision; the simplest way is to leave
            :attr:`NormalisedCase.content_hash` unset and let the runner carry this one
            through.
        cursor: Opaque source cursor (offset, page token, date window) recorded on the
            checkpoint so an interrupted run can resume mid-window.
        title: Title from the discovery payload, for logging and the dry-run report.
        decision_date: Decision date from the discovery payload, if it carries one.
        source_url: Canonical URL of the document at the source.
        source_metadata: Anything else discovery exposed, passed through to
            :meth:`SourceConnector.fetch`.
    """

    source_id: str
    jurisdiction_code: str
    modified_at: datetime | None = None
    content_hash: str | None = None
    cursor: str | None = None
    title: str | None = None
    decision_date: date | None = None
    source_url: str | None = None
    source_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate the identifiers and the timestamp.

        Raises:
            ValueError: If the source identifier or jurisdiction code is blank, or the
                modification timestamp is naive.
        """
        if not self.source_id.strip():
            message = "Candidate.source_id must not be blank"
            raise ValueError(message)
        if not self.jurisdiction_code.strip():
            message = f"Candidate.jurisdiction_code must not be blank for {self.source_id!r}"
            raise ValueError(message)
        _require_aware(self.modified_at, field_name="Candidate.modified_at")


@dataclass(frozen=True, slots=True)
class RawDocument:
    """The verbatim source response for one candidate.

    Kept unmodified and stored alongside the parsed record (``docs/architecture.md`` rule
    2.6), so a later reclassification never has to go back to the courts.

    Attributes:
        candidate: The candidate this document was fetched for.
        payload: The response body exactly as the source returned it.
        media_format: Payload format, e.g. ``xml``, ``html``, ``json``, ``text``. Named for
            the ``case_document.format`` column it ends up in.
        source_url: URL the payload was retrieved from.
        retrieved_at: When it was retrieved, timezone-aware.
        source_metadata: Response metadata worth keeping, e.g. the ETag or content type.
    """

    candidate: Candidate
    payload: str
    media_format: str | None = None
    source_url: str | None = None
    retrieved_at: datetime = field(default_factory=utcnow)
    source_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Reject a naive retrieval timestamp.

        Raises:
            ValueError: If ``retrieved_at`` carries no timezone.
        """
        _require_aware(self.retrieved_at, field_name="RawDocument.retrieved_at")

    @property
    def source_id(self) -> str:
        """Return the identifier of the document this payload belongs to."""
        return self.candidate.source_id

    @property
    def byte_size(self) -> int:
        """Return the size of the payload in UTF-8 bytes."""
        return len(self.payload.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class NormalisedDocument:
    """One full text or attachment of a case, in one language and format.

    Maps onto a ``case_document`` row. A case has one of these per language the source
    publishes, which is why :attr:`NormalisedCase.full_text` has to join them.

    Attributes:
        doc_type: Kind of document. ``attachment`` and ``other`` exist so an unexpected kind
            is stored rather than discarded.
        language: ISO 639-1 code of this version, if known.
        full_text: Extracted plain text. Scanned by the filter chain and by full-text search.
        raw_payload: The source response for this document, verbatim.
        media_format: Payload format, e.g. ``xml``, ``html``, ``pdf``, ``text``.
        source_url: URL this version was retrieved from.
        source_metadata: Anything else the source exposed about this version.
    """

    doc_type: DocumentType = DocumentType.JUDGMENT
    language: str | None = None
    full_text: str | None = None
    raw_payload: str | None = None
    media_format: str | None = None
    source_url: str | None = None
    source_metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def has_text(self) -> bool:
        """Return whether this version carries text a filter stage can read."""
        return bool(self.full_text and self.full_text.strip())


@dataclass(frozen=True, slots=True)
class NormalisedParty:
    """A litigating party, mapping onto a ``party`` row.

    Attributes:
        name: Party name as the source gives it.
        role: Procedural role.
        party_type: Nature of the party, e.g. ``natural_person``, ``company``, ``authority``.
        ordinal: Position within its role, preserving the source's ordering.
        source_metadata: Anything else the source exposed about the party.
    """

    name: str
    role: PartyRole = PartyRole.OTHER
    party_type: str | None = None
    ordinal: int | None = None
    source_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NormalisedCitation:
    """An instrument or case a decision cites, mapping onto a ``citation`` row.

    Attributes:
        target_identifier: CELEX number, ECLI, or the raw reference when it is neither.
        citation_type: Relation as the source names it, e.g. ``cites``, ``interprets``.
        target_scheme: Identifier scheme of the target, e.g. ``celex``, ``ecli``, ``other``.
        target_title: Title of the cited instrument, if the source supplies one.
        target_url: URL of the cited instrument.
        source_metadata: Anything else the source exposed about the citation.
    """

    target_identifier: str
    citation_type: str = "cites"
    target_scheme: str | None = None
    target_title: str | None = None
    target_url: str | None = None
    source_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NormalisedCourt:
    """The court that decided a case, resolved against the ``court`` table.

    Identified by :attr:`source_identifier` — the vocabulary URI or code the jurisdiction's
    source uses — rather than by name, so a renamed court does not become a second row.

    :attr:`level` and :attr:`domain` are this project's own classification, normalised across
    jurisdictions so the API can filter on them. :attr:`source_type` is the source's own word
    for the same thing, kept **beside** the normalised pair rather than instead of it. The
    normalisation is lossy on purpose — Rechtspraak's ``Koninkrijksinstantie`` and
    ``AndereGerechtelijkeInstantie`` both flatten to level ``other`` — and the source states
    the type exactly once, when the vocabulary is read. Discarding it here would make
    recovering it a re-fetch rather than a migration, which is what cross-cutting rule 6 of
    ``docs/architecture.md`` exists to prevent.

    Attributes:
        source_identifier: The source's own identifier for the court, unique per jurisdiction.
        name: Human-readable name.
        level: Instance in the judicial hierarchy, e.g. ``supreme``, ``appeal``.
        domain: Subject-matter domain, e.g. ``administrative``, ``civil``.
        source_type: The source's own type for the court, verbatim and unflattened, e.g.
            ``Koninkrijksinstantie`` or ``Rechtbank``. ``None`` when the source states none.
        abbreviation: Short form used by the source.
        source_url: URL of the court in the source's vocabulary.
        source_metadata: Anything else the source's vocabulary exposed about the court that
            the schema has no column for, e.g. the dates between which it sat.
    """

    source_identifier: str
    name: str
    level: str | None = None
    domain: str | None = None
    source_type: str | None = None
    abbreviation: str | None = None
    source_url: str | None = None
    source_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NormalisedCase:
    """A source document mapped onto the schema of ``docs/architecture.md`` section 3.

    The connector's job ends here: everything the schema names has a field, and everything
    else the source exposed goes into :attr:`source_metadata` or a document's
    ``raw_payload``, so nothing the endpoint gave us is thrown away.

    This class satisfies the filter chain's ``FilterableDocument`` protocol
    (:mod:`plt.pipeline.filters.base`) structurally — ``jurisdiction_code``, ``title``,
    ``abstract``, ``subject`` and ``full_text``. The first four are plain fields; the last is
    a property, because the schema keeps full texts per language on ``case_document`` while a
    filter stage wants one text to scan.

    Attributes:
        source_id: ECLI (NL) or CELEX (EU). Unique together with the jurisdiction.
        jurisdiction_code: ``NL`` or ``EU``.
        source_system: Which source supplied the record, e.g. ``rechtspraak``, ``cellar``.
        title: Case title.
        abstract: Summary or headnote (the Dutch ``inhoudsindicatie``).
        subject: Subject-matter classification — the *rechtsgebied* for the Netherlands, the
            EUROVOC/subject-matter heading for the EU. A scored field in both shipped keyword
            lists; see the module docstring.
        decision_date: Date of the decision.
        filing_date: Date proceedings were brought, if the source states it.
        publication_date: Date the source published the document.
        case_numbers: Every case number the source lists.
        language: Primary language of the decision, ISO 639-1. Orders :attr:`full_text`.
        law_domain: Top-level law domain.
        law_subfield: Finer classification within the domain.
        procedure_type: Type of procedure, as the source names it.
        outcome: Outcome, as the source names it.
        source_url: Canonical URL of the decision.
        content_hash: Revision fingerprint. Leave ``None`` and the runner derives one — from
            the candidate's hash when discovery supplied it, otherwise from the normalised
            content. Set it only when the source publishes a revision identifier of its own.
        court: The deciding court, resolved or created on persistence.
        documents: Full texts and attachments, one per language and kind.
        parties: Litigating parties.
        citations: Instruments and cases cited.
        source_metadata: Everything the source exposed that has no column of its own.
        is_published: Editorial switch; connectors leave it ``True``.
    """

    source_id: str
    jurisdiction_code: str
    source_system: str
    title: str | None = None
    abstract: str | None = None
    subject: str | None = None
    decision_date: date | None = None
    filing_date: date | None = None
    publication_date: date | None = None
    case_numbers: tuple[str, ...] = ()
    language: str | None = None
    law_domain: LawDomain | None = None
    law_subfield: str | None = None
    procedure_type: str | None = None
    outcome: str | None = None
    source_url: str | None = None
    content_hash: str | None = None
    court: NormalisedCourt | None = None
    documents: tuple[NormalisedDocument, ...] = ()
    parties: tuple[NormalisedParty, ...] = ()
    citations: tuple[NormalisedCitation, ...] = ()
    source_metadata: Mapping[str, Any] = field(default_factory=dict)
    is_published: bool = True
    #: Joined text of :attr:`documents`, computed once in ``__post_init__``.
    _full_text: str | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Validate the identifiers and materialise the joined full text.

        Raises:
            ValueError: If the source identifier, jurisdiction code or source system is blank.
        """
        if not self.source_id.strip():
            message = "NormalisedCase.source_id must not be blank"
            raise ValueError(message)
        if not self.jurisdiction_code.strip():
            message = f"NormalisedCase.jurisdiction_code must not be blank for {self.source_id!r}"
            raise ValueError(message)
        if not self.source_system.strip():
            message = f"NormalisedCase.source_system must not be blank for {self.source_id!r}"
            raise ValueError(message)
        object.__setattr__(self, "_full_text", self._join_documents())

    def _join_documents(self) -> str | None:
        """Join the text-bearing documents into the single text a filter stage reads.

        The case's own language comes first, the rest follow in a stable order, so the text a
        stage sees does not depend on the order the connector happened to build the tuple in.
        A single document is passed through by reference rather than copied, which is the
        common case for the Netherlands and keeps peak memory at one judgment.

        Returns:
            The joined text, or ``None`` when no document carries any.
        """
        texts = [document for document in self.documents if document.has_text]
        if not texts:
            return None
        if len(texts) == 1:
            return texts[0].full_text
        primary = (self.language or "").lower()
        ordered = sorted(
            enumerate(texts),
            key=lambda pair: (
                (pair[1].language or "").lower() != primary,
                (pair[1].language or ""),
                pair[0],
            ),
        )
        return _TEXT_SEPARATOR.join(str(document.full_text) for _, document in ordered)

    @property
    def full_text(self) -> str | None:
        """Return every language version's text, joined, for the filter chain.

        Returns:
            The joined text, or ``None`` when the case carries no document text. Offsets
            reported by a filter stage refer to this joined text; the snippets stored with a
            match are taken from it, so they stay readable evidence either way.
        """
        return self._full_text

    @property
    def text_languages(self) -> tuple[str | None, ...]:
        """Return the languages of the text-bearing documents, in document order."""
        return tuple(document.language for document in self.documents if document.has_text)


@dataclass(slots=True)
class SourceTraffic:
    """How much a run asked of its source, and how hard the source pushed back.

    Politeness is a requirement rather than a courtesy (``docs/architecture.md`` rule 2.5),
    and a requirement nobody can see the effect of is one nobody can hold the project to.
    These are the numbers that make it visible after the fact: how many requests left the
    process, how often one had to be repeated, and whether the source ever asked us to wait —
    which is the part an operator of a public endpoint would want to see honoured.

    Counted by :class:`plt.pipeline.http.PoliteClient`, which is the only place requests are
    sent from, and read back through :attr:`SourceConnector.traffic`.

    Attributes:
        requests: Requests actually sent, retries included.
        retries: Requests that had to be sent again after a failure worth retrying.
        retry_after_waits: Times a ``Retry-After`` header was obeyed as sent.
        retry_after_seconds: Total seconds waited because a source asked for them.
        backoff_seconds: Total seconds spent backing off, ``Retry-After`` waits included.
    """

    requests: int = 0
    retries: int = 0
    retry_after_waits: int = 0
    retry_after_seconds: float = 0.0
    backoff_seconds: float = 0.0


class SourceConnector(ABC):
    """One jurisdiction's data source.

    A subclass is the *only* code a new jurisdiction needs, besides its keyword list. It
    owns everything source-specific: endpoints (read from :class:`~plt.config.Settings`,
    never hard-coded), paging, the identifier scheme, and the mapping onto the schema.

    Politeness is not the subclass's problem: fetch through
    :class:`plt.pipeline.http.PoliteClient`, which applies the configured request rate,
    backoff with jitter on 429 and 5xx, and the descriptive ``User-Agent``.

    Attributes:
        jurisdiction_code: Jurisdiction this connector serves, e.g. ``NL``. The registry
            keys on it, so it is a class attribute.
        name: Stable connector name, e.g. ``rechtspraak``. Written to ``ingest_run.connector``
            and used as the primary key of ``ingest_checkpoint``, so renaming one restarts
            its checkpoint.
    """

    jurisdiction_code: ClassVar[str]
    name: ClassVar[str]

    def __init__(self, settings: Settings | None = None) -> None:
        """Bind the connector to its configuration.

        Args:
            settings: Settings supplying endpoints, timeouts, rates and page sizes. Defaults
                to the process-wide settings. The registry always passes this, so a subclass
                that overrides ``__init__`` should keep the parameter.
        """
        self._settings = settings if settings is not None else get_settings()

    @property
    def settings(self) -> Settings:
        """Return the settings this connector reads its configuration from."""
        return self._settings

    @property
    def traffic(self) -> SourceTraffic | None:
        """Return what this connector has asked of its source so far.

        A connector that fetches through :class:`plt.pipeline.http.PoliteClient` returns that
        client's running count, which is what lets a run report the rate it worked at and
        every ``Retry-After`` it honoured.

        Returns:
            The counts, or ``None`` when the connector does not report them — the default, so
            a source reached by some other means, or a fake in a test, is honestly recorded
            as "not reported" rather than as a run that sent no requests at all.
        """
        return None

    @abstractmethod
    def discover(self, since: datetime | None, until: datetime | None) -> Iterator[Candidate]:
        """Yield candidate identifiers with light metadata, oldest first, streaming.

        Implementations must page through the source rather than materialise the window:
        the runner consumes this lazily and commits per batch, and that is what keeps memory
        flat over a run of any size (``docs/architecture.md`` rule 2.3).

        Oldest first matters as well — the checkpoint advances to the newest item
        successfully processed, so out-of-order yields would make an interrupted run resume
        from the wrong place.

        Args:
            since: Only yield documents modified at or after this instant. ``None`` means no
                lower bound; the runner passes the stored checkpoint when the caller gave
                none. Treat it as inclusive: the overlap is absorbed by deduplication, and
                an exclusive bound risks dropping a document modified in the same second.
            until: Only yield documents modified at or before this instant, or ``None``.

        Yields:
            One :class:`Candidate` per document in the window.

        Raises:
            SourceUnavailableError: If the source cannot be enumerated at all.
        """

    def enumerate_identifiers(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> Iterator[Candidate]:
        """Yield the identifiers the source holds, by the cheapest route it offers.

        This is **not** discovery. Discovery answers "what has changed, and what do I need to
        know about it to decide whether to fetch it" — for CELLAR that means a ``GROUP BY``
        over a sector with optional expression joins, counted per window and paged. This
        answers only "what exists", which every source can say far more cheaply, and it is
        what lets a partial corpus be repaired by listing, diffing against the store and
        fetching the difference, instead of replaying thousands of discovery queries over the
        cases already on disk (``docs/architecture.md`` rule 2.10).

        The candidates it yields carry the identifier and whatever else the listing states for
        free. They are **not** required to carry a modification timestamp or a revision hash,
        and a caller must not treat them as a discovery result: nothing here says whether a
        case has changed, only that the source has one.

        Args:
            since: Narrow the listing to identifiers modified at or after this instant, where
                the source can express that without becoming expensive. ``None`` lists
                everything the source holds, which is the cheap case and the usual one.
            until: Upper bound, on the same terms.

        Yields:
            One :class:`Candidate` per identifier, in whatever order the source lists them
            most cheaply — which is not necessarily oldest first, because a listing is not
            resumed against a checkpoint the way a window is.

        Raises:
            IdentifierListUnavailableError: If this source has no listing cheaper than
                discovery. The default, so a connector says so by saying nothing.
            SourceUnavailableError: If the source cannot be listed at all.
        """
        del since, until
        message = (
            f"{self.name} cannot list its identifiers without walking discovery, so a "
            f"targeted repair would cost more than the walk it replaces"
        )
        raise IdentifierListUnavailableError(message)

    @abstractmethod
    def fetch(self, candidate: Candidate) -> RawDocument:
        """Retrieve the full document for one candidate, verbatim.

        Args:
            candidate: The candidate to retrieve.

        Returns:
            The unmodified source response, wrapped in a :class:`RawDocument`.

        Raises:
            DocumentUnavailableError: If this one document cannot be retrieved. The run
                continues with the next candidate.
            SourceUnavailableError: If the source as a whole has become unusable.
        """

    @abstractmethod
    def normalise(self, raw: RawDocument) -> NormalisedCase:
        """Map a raw payload onto the schema, keeping everything else.

        Args:
            raw: The payload returned by :meth:`fetch`.

        Returns:
            The normalised case. Fields the schema names are populated; anything else the
            source exposed goes into ``source_metadata`` or a document's ``raw_payload``.

        Raises:
            DocumentUnavailableError: If the payload cannot be parsed. The run continues.
        """

    def iter_courts(self) -> Iterator[NormalisedCourt]:
        """Yield the courts of this source's controlled vocabulary, for reference seeding.

        ``plt seed-vocabularies`` calls this and upserts what it gets on
        ``(jurisdiction_code, source_identifier)``, so seeding is idempotent and no court name
        is written into the code. A source that publishes no such vocabulary inherits this
        empty implementation and is simply reported as having nothing to seed, which is why
        the method is concrete rather than abstract.

        Yields:
            One :class:`NormalisedCourt` per court the source's vocabulary lists.

        Raises:
            SourceUnavailableError: If the vocabulary exists but cannot be read.
        """
        return iter(())

    def close(self) -> None:
        """Release whatever the connector holds open.

        The runner calls this in a ``finally`` block, including when a run is interrupted.
        A connector holding an HTTP client closes it here; one holding nothing inherits this
        no-op, which is why the method is concrete rather than abstract.
        """
        return None

    def __enter__(self) -> Self:
        """Return the connector, so it can be used as a context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connector on leaving the context."""
        del exc_type, exc, traceback
        self.close()

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs and test failures."""
        return f"{type(self).__name__}(name={self.name!r}, jurisdiction={self.jurisdiction_code!r})"
