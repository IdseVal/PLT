"""SQLAlchemy ORM models.

The table set is fixed by the contract in ``docs/architecture.md`` section 3. Models inherit
from :class:`plt.db.base.Base`, use SQLAlchemy 2.0 typed declarative mapping and store
timezone-aware UTC timestamps through :class:`plt.db.base.UtcDateTime`.

Two rules run through the whole schema:

1. **Deduplication.** ``case`` carries ``UNIQUE (jurisdiction_code, source_id)`` — the ECLI
   for the Netherlands, the CELEX number for the EU — enforced by the database, not by
   application code. ``content_hash`` distinguishes an unchanged re-fetch (touch
   ``last_seen_at``) from a genuine upstream revision (update in place, bump ``revision``).
2. **Keep everything the source gave us.** Every field a connector receives is persisted:
   the ones the schema names get a column, the rest go into that table's ``source_metadata``
   JSON, and the verbatim response body goes into ``case_document.raw_payload``. Later
   reclassification work therefore never needs a re-fetch (core document sections 2.2, 2.6).

Child rows (documents, parties, topics, keyword matches, citations) are deleted with their
case both at the database level (``ON DELETE CASCADE``) and in the ORM unit of work, so the
behaviour is the same whether a case is removed through a session or through SQL.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from plt.db.base import Base, UtcDateTime, portable_enum, utcnow

__all__ = [
    "Case",
    "CaseDocument",
    "CaseTopic",
    "Citation",
    "Court",
    "DocumentType",
    "IngestCheckpoint",
    "IngestRun",
    "IngestStatus",
    "Jurisdiction",
    "JurisdictionType",
    "KeywordMatch",
    "LawDomain",
    "Party",
    "PartyRole",
    "Topic",
    "TopicAssignmentSource",
]

# Column widths. Identifiers are bounded so PostgreSQL indexes stay small; free text is
# unbounded ``TEXT`` because source abstracts and judgments have no useful upper limit.
_CODE_LEN = 8
_IDENTIFIER_LEN = 255
_LABEL_LEN = 255
_SHORT_LEN = 64
_URL_LEN = 2048


class JurisdictionType(enum.StrEnum):
    """Whether a jurisdiction is a state or a supranational legal order.

    The EU is a jurisdiction in its own right, not an aggregation of its member states
    (core document section 3.3), which is why this distinction is stored rather than
    inferred.
    """

    STATE = "state"
    SUPRANATIONAL = "supranational"


class LawDomain(enum.StrEnum):
    """Top-level law domain, classification label 2 of core document section 2.2."""

    PUBLIC = "public"
    PRIVATE = "private"
    CRIMINAL = "criminal"
    OTHER = "other"


class DocumentType(enum.StrEnum):
    """Kind of document attached to a case.

    ``judgment``, ``opinion`` and ``summary`` are the three named in the architecture
    contract; ``attachment`` and ``other`` exist so that a connector meeting an unexpected
    document kind stores it instead of discarding it.
    """

    JUDGMENT = "judgment"
    OPINION = "opinion"
    SUMMARY = "summary"
    ATTACHMENT = "attachment"
    OTHER = "other"


class PartyRole(enum.StrEnum):
    """Procedural role of a litigating party, classification label 4."""

    APPLICANT = "applicant"
    DEFENDANT = "defendant"
    INTERVENER = "intervener"
    OTHER = "other"


class TopicAssignmentSource(enum.StrEnum):
    """How a topic came to be attached to a case."""

    PIPELINE = "pipeline"
    MANUAL = "manual"


class IngestStatus(enum.StrEnum):
    """Terminal (or current) state of a pipeline run.

    ``interrupted`` is written when a run catches ``SIGINT`` and shuts down gracefully;
    together with ``failed`` it marks the runs that must not advance the checkpoint
    (architecture section 7).
    """

    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class Jurisdiction(Base):
    """One legal order the tracker follows. ``NL`` and ``EU`` are seeded by migration.

    Adding a jurisdiction is a data change, not a code change (core document section 3.3):
    a row here, a keyword list in ``data/keywords/`` and a connector.
    """

    __tablename__ = "jurisdiction"

    code: Mapped[str] = mapped_column(String(_CODE_LEN), primary_key=True)
    name: Mapped[str] = mapped_column(String(_LABEL_LEN), nullable=False)
    type: Mapped[JurisdictionType] = mapped_column(
        portable_enum(JurisdictionType, "jurisdiction_type"),
        nullable=False,
    )
    #: ISO 3166-1 alpha-2 code; ``None`` for supranational orders, which have none.
    iso_alpha2: Mapped[str | None] = mapped_column(String(2))
    #: Identifier of the shape (or, for the EU, the North Sea logo) on the frontend map.
    map_feature_id: Mapped[str | None] = mapped_column(String(_SHORT_LEN))
    #: Default working language, used when a source omits one.
    default_language: Mapped[str | None] = mapped_column(String(_SHORT_LEN))
    #: Inactive jurisdictions stay visible on the map but are not ingested.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    courts: Mapped[list[Court]] = relationship(
        back_populates="jurisdiction", cascade="all, delete-orphan", passive_deletes=True
    )
    cases: Mapped[list[Case]] = relationship(
        back_populates="jurisdiction", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs and test failures."""
        return f"Jurisdiction(code={self.code!r}, name={self.name!r})"


class Court(Base):
    """A court or instance, seeded from a source's controlled vocabulary.

    ``source_identifier`` is the vocabulary URI or code used by that jurisdiction's source
    (a ``data.rechtspraak.nl`` instantie URI for the Netherlands), unique within the
    jurisdiction so a connector can resolve a court without a name match.
    """

    __tablename__ = "court"
    __table_args__ = (
        UniqueConstraint("jurisdiction_code", "source_identifier"),
        Index("ix_court_jurisdiction_code", "jurisdiction_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    jurisdiction_code: Mapped[str] = mapped_column(
        String(_CODE_LEN),
        ForeignKey("jurisdiction.code", ondelete="CASCADE"),
        nullable=False,
    )
    source_identifier: Mapped[str] = mapped_column(String(_IDENTIFIER_LEN), nullable=False)
    name: Mapped[str] = mapped_column(String(_LABEL_LEN), nullable=False)
    #: Instance in the judicial hierarchy, e.g. ``supreme``, ``appeal``, ``first_instance``.
    level: Mapped[str | None] = mapped_column(String(_SHORT_LEN))
    #: Subject-matter domain of the court, e.g. ``administrative``, ``civil``.
    domain: Mapped[str | None] = mapped_column(String(_SHORT_LEN))
    abbreviation: Mapped[str | None] = mapped_column(String(_SHORT_LEN))
    source_url: Mapped[str | None] = mapped_column(String(_URL_LEN))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    jurisdiction: Mapped[Jurisdiction] = relationship(back_populates="courts")
    cases: Mapped[list[Case]] = relationship(back_populates="court")

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs and test failures."""
        return f"Court(id={self.id!r}, jurisdiction_code={self.jurisdiction_code!r})"


class Case(Base):
    """A single decision — the central entity of the tracker.

    ``(jurisdiction_code, source_id)`` is the deduplication key and is unique at the
    database level. Anything the source exposes that has no column here belongs in
    ``source_metadata``; the verbatim payload belongs in
    :attr:`CaseDocument.raw_payload`.
    """

    __tablename__ = "case"
    __table_args__ = (
        UniqueConstraint("jurisdiction_code", "source_id"),
        Index("ix_case_jurisdiction_code_decision_date", "jurisdiction_code", "decision_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    jurisdiction_code: Mapped[str] = mapped_column(
        String(_CODE_LEN),
        ForeignKey("jurisdiction.code", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: ECLI (NL) or CELEX (EU). Unique together with the jurisdiction.
    source_id: Mapped[str] = mapped_column(String(_IDENTIFIER_LEN), nullable=False)
    #: Which source system supplied the record, e.g. ``rechtspraak`` or ``cellar``.
    source_system: Mapped[str] = mapped_column(String(_SHORT_LEN), nullable=False)
    court_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("court.id", ondelete="SET NULL"), index=True
    )

    title: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    decision_date: Mapped[date | None] = mapped_column(Date, index=True)
    filing_date: Mapped[date | None] = mapped_column(Date)
    publication_date: Mapped[date | None] = mapped_column(Date)
    #: Every case number the source lists, as a JSON array of strings.
    case_numbers: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    #: Primary language of the decision as an ISO 639-1 code.
    language: Mapped[str | None] = mapped_column(String(_SHORT_LEN), index=True)
    law_domain: Mapped[LawDomain | None] = mapped_column(portable_enum(LawDomain, "law_domain"))
    law_subfield: Mapped[str | None] = mapped_column(String(_LABEL_LEN))
    procedure_type: Mapped[str | None] = mapped_column(String(_LABEL_LEN))
    outcome: Mapped[str | None] = mapped_column(String(_LABEL_LEN))
    source_url: Mapped[str | None] = mapped_column(String(_URL_LEN))

    #: Hash over the normalised source content; detects genuine upstream revisions.
    content_hash: Mapped[str | None] = mapped_column(String(_SHORT_LEN), index=True)
    #: Incremented whenever a re-fetch produced a different ``content_hash``.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    #: Everything the endpoint exposed that has no column of its own.
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    #: Editorial switch: unpublished cases are hidden from the public API.
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    jurisdiction: Mapped[Jurisdiction] = relationship(back_populates="cases")
    court: Mapped[Court | None] = relationship(back_populates="cases")
    documents: Mapped[list[CaseDocument]] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True
    )
    parties: Mapped[list[Party]] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True
    )
    topics: Mapped[list[CaseTopic]] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True
    )
    keyword_matches: Mapped[list[KeywordMatch]] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True
    )
    citations: Mapped[list[Citation]] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs and test failures."""
        return f"Case(id={self.id!r}, jurisdiction_code={self.jurisdiction_code!r}, source_id={self.source_id!r})"  # noqa: E501


class CaseDocument(Base):
    """A full text or attachment belonging to a case, in one language and format.

    ``raw_payload`` holds the source response verbatim (architecture rule 2.6): the XML from
    ``data.rechtspraak.nl`` or the CELLAR notice. It is what makes later reclassification
    possible without going back to the courts.
    """

    __tablename__ = "case_document"
    __table_args__ = (Index("ix_case_document_case_id_language", "case_id", "language"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("case.id", ondelete="CASCADE"), nullable=False, index=True
    )
    language: Mapped[str | None] = mapped_column(String(_SHORT_LEN))
    doc_type: Mapped[DocumentType] = mapped_column(
        portable_enum(DocumentType, "document_type"), nullable=False
    )
    #: Payload format, e.g. ``xml``, ``html``, ``pdf``, ``text``.
    format: Mapped[str | None] = mapped_column(String(_SHORT_LEN))
    #: Extracted plain text, used by full-text search.
    full_text: Mapped[str | None] = mapped_column(Text)
    #: The source response, unmodified.
    raw_payload: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(_URL_LEN))
    #: Hash of ``raw_payload``; lets a re-fetch skip an unchanged document.
    content_hash: Mapped[str | None] = mapped_column(String(_SHORT_LEN))
    byte_size: Mapped[int | None] = mapped_column(Integer)
    retrieved_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    case: Mapped[Case] = relationship(back_populates="documents")

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs and test failures."""
        return f"CaseDocument(id={self.id!r}, case_id={self.case_id!r}, doc_type={self.doc_type!r})"


class Party(Base):
    """A litigating party, classification label 4 of core document section 2.2."""

    __tablename__ = "party"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("case.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[PartyRole] = mapped_column(
        portable_enum(PartyRole, "party_role"), nullable=False, default=PartyRole.OTHER
    )
    #: Nature of the party, e.g. ``natural_person``, ``company``, ``authority``, ``ngo``.
    party_type: Mapped[str | None] = mapped_column(String(_SHORT_LEN))
    #: Position within its role, preserving the order the source listed parties in.
    ordinal: Mapped[int | None] = mapped_column(Integer)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    case: Mapped[Case] = relationship(back_populates="parties")

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs and test failures."""
        return f"Party(id={self.id!r}, case_id={self.case_id!r}, role={self.role!r})"


class Topic(Base):
    """A topic in the (extensible) classification tree, label 6 of section 2.2.

    Topics form a hierarchy through ``parent_id``. The vocabulary is developed during the
    project, so it is data rather than an enum.
    """

    __tablename__ = "topic"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: Stable, URL-safe identifier used by the API and the filter UI.
    slug: Mapped[str] = mapped_column(String(_LABEL_LEN), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(_LABEL_LEN), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("topic.id", ondelete="SET NULL"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    parent: Mapped[Topic | None] = relationship(back_populates="children", remote_side="Topic.id")
    children: Mapped[list[Topic]] = relationship(back_populates="parent")
    cases: Mapped[list[CaseTopic]] = relationship(
        back_populates="topic", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs and test failures."""
        return f"Topic(id={self.id!r}, slug={self.slug!r})"


class CaseTopic(Base):
    """Association between a case and a topic, with provenance.

    ``assigned_by`` records whether the pipeline or a content manager attached the topic,
    which is what makes it safe for a re-run to replace its own assignments without
    discarding manual ones.
    """

    __tablename__ = "case_topic"

    case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("case.id", ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("topic.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    #: Classifier confidence in ``[0, 1]``; ``None`` for a manual assignment.
    confidence: Mapped[float | None] = mapped_column(Float)
    assigned_by: Mapped[TopicAssignmentSource] = mapped_column(
        portable_enum(TopicAssignmentSource, "topic_assignment_source"),
        nullable=False,
        default=TopicAssignmentSource.PIPELINE,
    )
    assigned_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="topics")
    topic: Mapped[Topic] = relationship(back_populates="cases")

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs and test failures."""
        return f"CaseTopic(case_id={self.case_id!r}, topic_id={self.topic_id!r})"


class KeywordMatch(Base):
    """A keyword-list term that matched a case, and where it matched.

    This table is how the content manager measures precision and recall of the curated
    lists (core document section 2.5, point 3), so a run records every match, not just the
    fact that a case passed the filter.
    """

    __tablename__ = "keyword_match"
    __table_args__ = (Index("ix_keyword_match_term_id_list_version", "term_id", "list_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("case.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Identifier of the term inside the jurisdiction's keyword list.
    term_id: Mapped[str] = mapped_column(String(_IDENTIFIER_LEN), nullable=False)
    #: The literal term as written in the list, kept so a report reads without a join.
    term: Mapped[str | None] = mapped_column(String(_LABEL_LEN))
    #: Version of the keyword list that produced the match.
    list_version: Mapped[str | None] = mapped_column(String(_SHORT_LEN))
    #: Field the term matched in, e.g. ``title``, ``abstract``, ``full_text``.
    field: Mapped[str | None] = mapped_column(String(_SHORT_LEN))
    weight_applied: Mapped[float | None] = mapped_column(Float)
    match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: Surrounding text, so a reviewer can judge the match without opening the case.
    snippet: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="keyword_matches")

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs and test failures."""
        return f"KeywordMatch(id={self.id!r}, case_id={self.case_id!r}, term_id={self.term_id!r})"


class Citation(Base):
    """An instrument or case cited by a decision.

    ``target_identifier`` is a CELEX number, an ECLI or, failing both, the raw reference the
    source gave; ``target_scheme`` says which. The pair with ``citation_type`` is unique per
    case so a re-run cannot duplicate citations.
    """

    __tablename__ = "citation"
    __table_args__ = (
        UniqueConstraint("case_id", "target_identifier", "citation_type"),
        Index("ix_citation_target_identifier", "target_identifier"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("case.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_identifier: Mapped[str] = mapped_column(String(_IDENTIFIER_LEN), nullable=False)
    #: Identifier scheme of the target, e.g. ``celex``, ``ecli``, ``other``.
    target_scheme: Mapped[str | None] = mapped_column(String(_SHORT_LEN))
    #: Relation as the source names it, e.g. ``cites``, ``interprets``, ``amends``.
    citation_type: Mapped[str] = mapped_column(String(_SHORT_LEN), nullable=False)
    target_title: Mapped[str | None] = mapped_column(Text)
    target_url: Mapped[str | None] = mapped_column(String(_URL_LEN))
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="citations")

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs and test failures."""
        return (
            f"Citation(id={self.id!r}, case_id={self.case_id!r}, target={self.target_identifier!r})"
        )


class IngestRun(Base):
    """One execution of the pipeline for one jurisdiction.

    The counters are the run report the content manager reads; ``checkpoint_before`` and
    ``checkpoint_after`` make it possible to replay or rewind a window. A run that did not
    finish successfully must leave the stored checkpoint untouched (architecture section 7).
    """

    __tablename__ = "ingest_run"
    __table_args__ = (
        CheckConstraint("fetched_count >= 0", name="fetched_count_non_negative"),
        Index("ix_ingest_run_jurisdiction_code_started_at", "jurisdiction_code", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    jurisdiction_code: Mapped[str] = mapped_column(
        String(_CODE_LEN),
        ForeignKey("jurisdiction.code", ondelete="CASCADE"),
        nullable=False,
    )
    connector: Mapped[str] = mapped_column(String(_SHORT_LEN), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    status: Mapped[IngestStatus] = mapped_column(
        portable_enum(IngestStatus, "ingest_status"), nullable=False, default=IngestStatus.RUNNING
    )
    #: A dry run writes a match report and no case rows (architecture section 4).
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: Checkpoint state at the start and end of the run, as stored in ``ingest_checkpoint``.
    checkpoint_before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    checkpoint_after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: First fatal error, truncated by the caller. Never contains credentials.
    error_message: Mapped[str | None] = mapped_column(Text)

    jurisdiction: Mapped[Jurisdiction] = relationship()

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs and test failures."""
        return f"IngestRun(id={self.id!r}, connector={self.connector!r}, status={self.status!r})"


class IngestCheckpoint(Base):
    """The resumable position of one connector.

    Keyed on the connector rather than the jurisdiction because a jurisdiction may
    eventually have more than one source. A run that fails or is interrupted leaves this row
    as it found it.
    """

    __tablename__ = "ingest_checkpoint"

    connector: Mapped[str] = mapped_column(String(_SHORT_LEN), primary_key=True)
    jurisdiction_code: Mapped[str] = mapped_column(
        String(_CODE_LEN),
        ForeignKey("jurisdiction.code", ondelete="CASCADE"),
        nullable=False,
    )
    #: Highest source modification timestamp processed; the next run starts here.
    last_modified_seen: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: Opaque source cursor (offset, page token, date window) for mid-window resumption.
    last_cursor: Mapped[str | None] = mapped_column(String(_IDENTIFIER_LEN))
    #: Identifier of the last document processed, for connectors without a cursor.
    last_source_id: Mapped[str | None] = mapped_column(String(_IDENTIFIER_LEN))
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    jurisdiction: Mapped[Jurisdiction] = relationship()

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs and test failures."""
        return f"IngestCheckpoint(connector={self.connector!r}, cursor={self.last_cursor!r})"
