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
    "CaseReview",
    "CaseReviewDecision",
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
    "ReviewDecision",
    "ReviewStatus",
    "Subscriber",
    "SubscriberStatus",
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

#: Longest address the schema stores. RFC 5321 bounds a reverse-path at 256 octets including
#: the angle brackets, which leaves 254 for the address itself.
_EMAIL_LEN = 254


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


class ReviewDecision(enum.StrEnum):
    """The verdict a content manager reached on a flagged case.

    Both outcomes are recorded. A rejection is not a deletion: the case stops being
    published, its ``keyword_match`` evidence stays, and the decision is part of the audit
    trail that explains why the tracker does not show it (core document section 2.7).
    """

    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ReviewStatus(enum.StrEnum):
    """Where a flagged case stands in the review queue.

    ``pending`` is the queue itself. ``confirmed`` and ``rejected`` mirror the decision that
    was taken. ``withdrawn`` covers the case that left the review band on a later evaluation
    before anyone had decided on it: nothing was judged, so nothing is claimed, and it is
    kept rather than deleted because a queue that silently loses items is not auditable.

    A decided case whose upstream revision falls in the band again returns to ``pending``
    while its previous decision stays on the row — re-flagged rather than silently
    inheriting the old verdict.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class SubscriberStatus(enum.StrEnum):
    """Where an address stands in the double opt-in lifecycle.

    ``pending`` is an address that has been submitted and has **not** confirmed. It receives
    nothing but its own confirmation message: without that click, anyone could subscribe
    anyone. ``confirmed`` is the only state a digest is ever sent to. ``unsubscribed`` is a
    withdrawal, kept rather than deleted so a later re-subscription is a state change on one
    row and the record of consent stays coherent.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    UNSUBSCRIBED = "unsubscribed"


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

    #: Total weight the filter chain awarded at the last evaluation. Recorded rather than
    #: recomputed, so the flag below can be read back against the score that produced it.
    filter_score: Mapped[float | None] = mapped_column(Float)
    #: Whether that score fell in the keyword list's review band. A flagged case is
    #: published exactly like any other: the flag adds a review, it does not withhold the
    #: case (core document section 2.7).
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

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
    review: Mapped[CaseReview | None] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
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


class CaseReview(Base):
    """One case in the review queue, with the standing decision on it.

    Created when the filter chain flags a case as borderline (core document section 2.7) and
    kept afterwards, whatever the outcome: the row is the audit trail of why a case is, or is
    no longer, in the tracker. One row per case, so ``case_id`` is unique.

    Three properties are load-bearing:

    1. **A decision survives re-ingestion.** The weekly run rewrites the child rows it owns —
       documents, parties, citations, keyword matches — but never this one. An unchanged case
       is not even re-evaluated, and a re-run over the same content leaves the row untouched.
    2. **A genuine upstream revision re-opens the review.** When the content hash differs from
       :attr:`flagged_content_hash` and the new score is in the band again, the status returns
       to ``pending`` while :attr:`decision`, :attr:`decided_by` and :attr:`decided_at` stay
       as they were. The reviewer therefore sees the previous verdict and that it no longer
       applies to the current text, instead of the new text silently inheriting it.
    3. **A rejection withholds publication without deleting anything.** The case row, its
       documents and its ``keyword_match`` evidence all remain; only ``case.is_published``
       goes false, and :attr:`suppressed_publication` records that the review is what did it,
       so a later confirmation can undo exactly that and nothing else.

    The reviewer may be a person or an agent, which is why :attr:`decided_by` is an opaque
    identifier and nothing here assumes a user account or a screen.
    """

    __tablename__ = "case_review"
    __table_args__ = (
        UniqueConstraint("case_id"),
        Index("ix_case_review_status_flagged_at", "status", "flagged_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("case.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[ReviewStatus] = mapped_column(
        portable_enum(ReviewStatus, "review_status"),
        nullable=False,
        default=ReviewStatus.PENDING,
    )

    #: The score, the threshold and the band ceiling that produced the flag, and the version
    #: of the list they came from. Stored together so the flag can be re-derived from the
    #: row alone — the repeatability requirement of core document section 2.8.
    score: Mapped[float | None] = mapped_column(Float)
    min_score: Mapped[float | None] = mapped_column(Float)
    band_ceiling: Mapped[float | None] = mapped_column(Float)
    list_version: Mapped[str | None] = mapped_column(String(_SHORT_LEN))
    #: The filter chain's own sentence about the case, as shown to the reviewer.
    reason: Mapped[str | None] = mapped_column(Text)

    flagged_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    #: ``case.revision`` and ``case.content_hash`` when the flag was last raised. The hash is
    #: what distinguishes a re-run over the same text from a genuine upstream revision.
    flagged_revision: Mapped[int | None] = mapped_column(Integer)
    flagged_content_hash: Mapped[str | None] = mapped_column(String(_SHORT_LEN))

    #: The standing decision, if one has been taken. Retained across a re-flag: the status
    #: says the case is back in the queue, these columns say what was last concluded.
    decision: Mapped[ReviewDecision | None] = mapped_column(
        portable_enum(ReviewDecision, "review_decision")
    )
    #: Who decided — a person or an agent. Opaque identifier, never a credential.
    decided_by: Mapped[str | None] = mapped_column(String(_IDENTIFIER_LEN))
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    decided_revision: Mapped[int | None] = mapped_column(Integer)
    decision_note: Mapped[str | None] = mapped_column(Text)
    #: Whether the standing rejection is what unpublished the case, so confirming it later
    #: restores publication only where the review withheld it.
    suppressed_publication: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    case: Mapped[Case] = relationship(back_populates="review")
    decisions: Mapped[list[CaseReviewDecision]] = relationship(
        back_populates="review",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CaseReviewDecision.decided_at",
    )

    @property
    def is_open(self) -> bool:
        """Return whether the item is awaiting a decision."""
        return self.status is ReviewStatus.PENDING

    def apply_publication(self, case: Case) -> None:
        """Withhold or restore the case's publication according to the standing decision.

        The single place the rule lives, because both the ingestion path and the decision
        endpoint have to apply it and they must not disagree. A rejection unpublishes; a
        confirmation republishes **only** what a rejection had withheld, so a case an editor
        unpublished for some other reason is not published by a reviewer confirming that it
        is about pesticides.

        Nothing is deleted either way: the case row, its documents and its ``keyword_match``
        evidence are untouched.

        Args:
            case: The case this review belongs to.
        """
        if self.decision is ReviewDecision.REJECTED:
            if case.is_published:
                self.suppressed_publication = True
            case.is_published = False
        elif self.suppressed_publication:
            case.is_published = True
            self.suppressed_publication = False

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs and test failures."""
        return f"CaseReview(id={self.id!r}, case_id={self.case_id!r}, status={self.status!r})"


class CaseReviewDecision(Base):
    """One decision taken on a review item — the append-only history.

    :class:`CaseReview` carries the *standing* decision so the queue can be listed in one
    query; this table carries every decision ever taken, including the ones a later upstream
    revision superseded. Rows are never updated and never deleted with anything but their
    case.
    """

    __tablename__ = "case_review_decision"
    __table_args__ = (
        Index("ix_case_review_decision_review_id_decided_at", "review_id", "decided_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("case_review.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision: Mapped[ReviewDecision] = mapped_column(
        portable_enum(ReviewDecision, "review_decision_outcome"), nullable=False
    )
    #: Who decided — a person or an agent.
    decided_by: Mapped[str] = mapped_column(String(_IDENTIFIER_LEN), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    note: Mapped[str | None] = mapped_column(Text)
    #: The revision and content hash the decision was taken on, so a superseded decision
    #: still says what it was about.
    case_revision: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(_SHORT_LEN))

    review: Mapped[CaseReview] = relationship(back_populates="decisions")

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs and test failures."""
        return (
            f"CaseReviewDecision(id={self.id!r}, review_id={self.review_id!r}, "
            f"decision={self.decision!r})"
        )


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


class Subscriber(Base):
    """One address on the mailing list.

    An email address is personal data under the GDPR, and Wageningen University is an EU
    institution, so the column list is a deliberate minimum: the address, where it stands, the
    timestamps that make the lifecycle auditable, and the stored half of its token. There is
    no name, no IP address, no user agent, no referrer and no delivery telemetry — the tracker
    cannot report who opened a digest because it never records it, and a message carries no
    tracking pixel and no redirected links.

    **The token is stored as a seed, not as a token.** ``token_seed`` is an unguessable random
    selector; the token in a link is ``<seed>.<verifier>``, where the verifier is an
    HMAC-SHA256 of the seed and the purpose under a server-side key
    (:mod:`plt.notifications.tokens`). A database dump therefore yields no working
    confirmation or unsubscribe link, and a link stays valid across every digest without the
    secret half of it ever being written down.

    **Nothing here may be listed.** No API endpoint reads this table for anything but a single
    address or a single seed, and the subscription endpoints answer identically whether or not
    a row exists — an endpoint that behaved differently for a known address would be an
    address-checking oracle for anyone with a word list.
    """

    __tablename__ = "subscriber"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: The address, normalised to lower case so one person cannot hold two rows.
    email: Mapped[str] = mapped_column(String(_EMAIL_LEN), nullable=False, unique=True)
    status: Mapped[SubscriberStatus] = mapped_column(
        portable_enum(SubscriberStatus, "subscriber_status"),
        nullable=False,
        default=SubscriberStatus.PENDING,
        index=True,
    )
    #: Selector half of the confirmation and unsubscribe tokens. Random, unique, and useless
    #: without the server key; rotated when an address re-subscribes, which retires the links
    #: of the previous subscription.
    token_seed: Mapped[str] = mapped_column(String(_SHORT_LEN), nullable=False, unique=True)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    #: When the last transactional message went to this address — a confirmation request or
    #: the note that it is already subscribed. Two jobs: it expires an unconfirmed
    #: subscription, and it throttles the public endpoint so it cannot be turned into a
    #: mail bomb aimed at a third party. Written on every branch, so the throttle itself
    #: cannot become a way of telling a known address from an unknown one.
    notice_sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: When consent was given. ``None`` until the confirmation link is used.
    confirmed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: When it was withdrawn. Kept so a withdrawal is auditable rather than a missing row.
    unsubscribed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: The last digest window this address was sent, which is what makes an interrupted send
    #: resumable: a re-run skips the recipients already served. Not a record of delivery, and
    #: not a record of anything the reader did.
    last_digest_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    @property
    def is_subscribed(self) -> bool:
        """Return whether this address has confirmed and may receive a digest."""
        return self.status is SubscriberStatus.CONFIRMED

    def __repr__(self) -> str:
        """Return a representation that never discloses the address.

        A repr reaches logs and test output, and ``docs/architecture.md`` rule 2.7 keeps
        personal data out of both, so the row is identified by its primary key alone.
        """
        return f"Subscriber(id={self.id!r}, status={self.status!r})"
