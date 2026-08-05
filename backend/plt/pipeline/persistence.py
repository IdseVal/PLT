"""Writing a normalised case, its documents and its matches into the schema.

The runner decides *what* happens to a document; this module performs it. Keeping the two
apart is what lets the runner stay jurisdiction-agnostic and lets a dry run exercise every
stage except this one.

Three operations, matching the three deduplication outcomes of
``docs/architecture.md`` section 3:

* :func:`insert_case` — a document not seen before.
* :func:`update_case` — a stored document whose content hash changed: updated **in place**,
  ``revision`` incremented, never inserted a second time.
* :func:`touch_last_seen` — a stored document that is unchanged: one column written, so the
  weekly re-run of a rolling window costs almost nothing.

Child rows the pipeline owns (documents, parties, citations, keyword matches) are replaced
wholesale on an update, because the source is authoritative for all of them and a revision
may have removed one. Manually assigned topics are left alone: ``case_topic.assigned_by``
distinguishes them, and a re-run must not discard a content manager's work. The same applies
to ``case_review``: the pipeline raises the flag and re-raises it after a genuine upstream
revision, but a decision recorded on it belongs to the content manager and is never
overwritten by a run — see :func:`apply_review`.

All writes go through the ORM unit of work — no SQL is composed here, and every value
reaches the database as a bound parameter.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from plt.db.base import utcnow
from plt.db.models import (
    Case,
    CaseDocument,
    CaseReview,
    Citation,
    Court,
    KeywordMatch,
    Party,
    ReviewStatus,
)
from plt.pipeline.base import NormalisedCase, NormalisedCourt
from plt.pipeline.filters.base import FilterResult
from plt.utils.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = [
    "PersistOutcome",
    "apply_review",
    "insert_case",
    "persist_case",
    "resolve_court",
    "touch_last_seen",
    "update_case",
]

log = get_logger(__name__)

#: Column widths from ``plt.db.models``; values longer than these are truncated rather than
#: rejected, because losing a case over an over-long court name would be the worse outcome.
_IDENTIFIER_LEN = 255
_LABEL_LEN = 255
_SHORT_LEN = 64

#: Marker appended to a truncated value, so a reader can see it was cut.
_ELLIPSIS = "…"


class PersistOutcome(enum.StrEnum):
    """What a persistence call did, for the run counters and the report."""

    #: A new case row was created.
    INSERTED = "inserted"
    #: An existing case row was updated in place and its revision incremented.
    UPDATED = "updated"
    #: Only ``last_seen_at`` was written.
    TOUCHED = "touched"


def _clip(value: str | None, limit: int) -> str | None:
    """Truncate a value to a column width.

    Args:
        value: The value to clip, or ``None``.
        limit: Maximum number of characters the column holds.

    Returns:
        The value, shortened with an ellipsis when it was too long.
    """
    if value is None or len(value) <= limit:
        return value
    return value[: limit - 1] + _ELLIPSIS


def touch_last_seen(session: Session, case_id: int, *, now: datetime | None = None) -> None:
    """Record that an unchanged case was seen again in this run.

    Args:
        session: Open database session.
        case_id: Primary key of the stored case.
        now: Timestamp to write. Defaults to the current UTC instant.
    """
    stored = session.get(Case, case_id)
    if stored is None:  # pragma: no cover - the fingerprint was read in the same session
        log.warning("case disappeared before last_seen_at could be touched", extra={"id": case_id})
        return
    stored.last_seen_at = now if now is not None else utcnow()


def resolve_court(
    session: Session,
    jurisdiction_code: str,
    court: NormalisedCourt | None,
) -> Court | None:
    """Find or create the court row a case belongs to.

    Courts are matched on ``(jurisdiction_code, source_identifier)`` — the source's own
    vocabulary identifier — and never on the name, so a renamed court updates its row
    instead of becoming a second one.

    Args:
        session: Open database session.
        jurisdiction_code: Jurisdiction the court belongs to.
        court: The court as the connector normalised it, or ``None``.

    Returns:
        The persistent court row, or ``None`` when the case names no court.
    """
    if court is None:
        return None
    identifier = _clip(court.source_identifier, _IDENTIFIER_LEN) or court.source_identifier
    existing = session.scalars(
        select(Court).where(
            Court.jurisdiction_code == jurisdiction_code,
            Court.source_identifier == identifier,
        )
    ).one_or_none()
    if existing is None:
        existing = Court(
            jurisdiction_code=jurisdiction_code,
            source_identifier=identifier,
            name=_clip(court.name, _LABEL_LEN) or identifier,
        )
        session.add(existing)
    else:
        existing.name = _clip(court.name, _LABEL_LEN) or existing.name
    existing.level = _clip(court.level, _SHORT_LEN)
    existing.domain = _clip(court.domain, _SHORT_LEN)
    existing.abbreviation = _clip(court.abbreviation, _SHORT_LEN)
    existing.source_url = court.source_url
    session.flush()
    return existing


def _apply_fields(
    row: Case,
    case: NormalisedCase,
    *,
    content_hash: str,
    court: Court | None,
    now: datetime,
) -> None:
    """Copy every schema field of a normalised case onto its row.

    Args:
        row: The case row to write to.
        case: The normalised case.
        content_hash: Revision fingerprint to store.
        court: Resolved court row, or ``None``.
        now: Timestamp used for ``last_seen_at``.
    """
    row.source_system = _clip(case.source_system, _SHORT_LEN) or case.source_system
    row.court = court
    row.title = case.title
    row.abstract = case.abstract
    row.decision_date = case.decision_date
    row.filing_date = case.filing_date
    row.publication_date = case.publication_date
    row.case_numbers = list(case.case_numbers)
    row.language = _clip(case.language, _SHORT_LEN)
    row.law_domain = case.law_domain
    row.law_subfield = _clip(case.law_subfield, _LABEL_LEN)
    row.procedure_type = _clip(case.procedure_type, _LABEL_LEN)
    row.outcome = _clip(case.outcome, _LABEL_LEN)
    row.source_url = case.source_url
    row.content_hash = content_hash
    row.last_seen_at = now
    row.is_published = case.is_published
    # ``subject`` has no column of its own in the schema contract, but it is a scored field
    # and worth keeping: it goes into source_metadata alongside everything else the source
    # exposed, rather than being dropped on the floor.
    metadata = dict(case.source_metadata)
    if case.subject is not None:
        metadata.setdefault("subject", case.subject)
    row.source_metadata = metadata


def _rebuild_children(row: Case, case: NormalisedCase, result: FilterResult, now: datetime) -> None:
    """Replace the child rows the pipeline owns with the ones this run produced.

    Args:
        row: The case row, already attached to the session.
        case: The normalised case supplying documents, parties and citations.
        result: The filter verdict supplying the keyword matches.
        now: Retrieval timestamp written on the documents.
    """
    row.documents = [
        CaseDocument(
            language=_clip(document.language, _SHORT_LEN),
            doc_type=document.doc_type,
            format=_clip(document.media_format, _SHORT_LEN),
            full_text=document.full_text,
            raw_payload=document.raw_payload,
            source_url=document.source_url,
            byte_size=len(document.raw_payload.encode("utf-8")) if document.raw_payload else None,
            retrieved_at=now,
            source_metadata=dict(document.source_metadata),
        )
        for document in case.documents
    ]
    row.parties = [
        Party(
            name=party.name,
            role=party.role,
            party_type=_clip(party.party_type, _SHORT_LEN),
            ordinal=party.ordinal if party.ordinal is not None else ordinal,
            source_metadata=dict(party.source_metadata),
        )
        for ordinal, party in enumerate(case.parties)
    ]
    row.citations = _unique_citations(case)
    row.keyword_matches = [
        KeywordMatch(
            term_id=_clip(match.term_id, _IDENTIFIER_LEN) or match.term_id,
            term=_clip(match.matched_text, _LABEL_LEN),
            list_version=_clip(match.list_version, _SHORT_LEN),
            field=_clip(match.field, _SHORT_LEN),
            weight_applied=match.weight_applied,
            match_count=match.occurrences,
            snippet=match.snippet,
        )
        for match in result.matches
    ]


def _list_version(result: FilterResult) -> str | None:
    """Return the keyword-list version a verdict was produced with.

    Args:
        result: The filter verdict.

    Returns:
        The version every match reports, or ``None`` when nothing matched.
    """
    for match in result.matches:
        return match.list_version
    return None


def apply_review(
    row: Case,
    result: FilterResult,
    *,
    content_hash: str,
    now: datetime,
) -> None:
    """Record the filter's review flag on a case, without disturbing a decision on it.

    The flag itself is deterministic: it is the verdict of the same list version on the same
    text, so re-running a window rewrites the row with the values it already held. What is
    *not* rewritten is the content manager's work, and the rules that protect it are:

    * **Same content, same flag, same row.** If the case's content hash is the one the flag
      was last raised on, the review is left exactly as it stands — including a decision
      already taken. This is what makes the weekly run safe to repeat.
    * **A genuine upstream revision re-opens the review.** A different content hash means the
      text a decision was taken on no longer exists, so the item returns to ``pending`` while
      the previous decision stays visible on the row. Inheriting a verdict silently is the
      failure this prevents.
    * **Leaving the band withdraws an undecided item.** A revision that scores clear of the
      band retires a *pending* item; a decision that has been taken stands regardless, and a
      standing rejection keeps withholding publication.

    Args:
        row: The case row, already carrying this run's revision.
        result: The filter verdict for this evaluation.
        content_hash: The revision fingerprint stored on the case.
        now: Timestamp used for the flag.
    """
    row.filter_score = result.score
    row.needs_review = result.needs_review
    review = row.review

    if result.needs_review:
        if review is None:
            review = CaseReview(status=ReviewStatus.PENDING)
            row.review = review
        if review.flagged_content_hash != content_hash:
            # The text this case was flagged on has changed, so a decision taken on the old
            # text no longer covers it: back into the queue, previous verdict retained.
            review.status = ReviewStatus.PENDING
            review.flagged_at = now
            review.flagged_revision = row.revision
            review.flagged_content_hash = content_hash
            review.score = result.score
            review.min_score = result.threshold
            review.band_ceiling = result.review_ceiling
            review.list_version = _clip(_list_version(result), _SHORT_LEN)
            review.reason = result.reason
    elif review is not None and review.is_open and review.decision is None:
        review.status = ReviewStatus.WITHDRAWN
        review.score = result.score
        review.reason = result.reason

    if review is not None:
        review.apply_publication(row)


def _unique_citations(case: NormalisedCase) -> list[Citation]:
    """Build the citation rows, dropping duplicates the source repeated.

    ``citation`` is unique on ``(case_id, target_identifier, citation_type)``; a source that
    lists the same instrument twice would otherwise abort the whole batch on a constraint
    violation.

    Args:
        case: The normalised case.

    Returns:
        One row per distinct citation, in source order.
    """
    seen: set[tuple[str, str]] = set()
    rows: list[Citation] = []
    for citation in case.citations:
        identifier = _clip(citation.target_identifier, _IDENTIFIER_LEN) or ""
        citation_type = _clip(citation.citation_type, _SHORT_LEN) or "cites"
        key = (identifier, citation_type)
        if not identifier or key in seen:
            continue
        seen.add(key)
        rows.append(
            Citation(
                target_identifier=identifier,
                target_scheme=_clip(citation.target_scheme, _SHORT_LEN),
                citation_type=citation_type,
                target_title=citation.target_title,
                target_url=citation.target_url,
                source_metadata=dict(citation.source_metadata),
            )
        )
    return rows


def insert_case(
    session: Session,
    case: NormalisedCase,
    result: FilterResult,
    *,
    content_hash: str,
    now: datetime | None = None,
) -> Case:
    """Insert a case that has not been seen before, with its children.

    Args:
        session: Open database session.
        case: The normalised case.
        result: The filter verdict, whose matches become ``keyword_match`` rows.
        content_hash: Revision fingerprint to store.
        now: Timestamp for ``first_seen_at``/``last_seen_at``. Defaults to now.

    Returns:
        The inserted row, flushed so its primary key is available.
    """
    moment = now if now is not None else utcnow()
    row = Case(
        jurisdiction_code=case.jurisdiction_code,
        source_id=_clip(case.source_id, _IDENTIFIER_LEN) or case.source_id,
        source_system=case.source_system,
        first_seen_at=moment,
        revision=1,
    )
    _apply_fields(
        row,
        case,
        content_hash=content_hash,
        court=resolve_court(session, case.jurisdiction_code, case.court),
        now=moment,
    )
    _rebuild_children(row, case, result, moment)
    apply_review(row, result, content_hash=content_hash, now=moment)
    session.add(row)
    session.flush()
    return row


def update_case(
    session: Session,
    case_id: int,
    case: NormalisedCase,
    result: FilterResult,
    *,
    content_hash: str,
    now: datetime | None = None,
) -> Case:
    """Update a stored case in place after an upstream revision.

    Args:
        session: Open database session.
        case_id: Primary key of the stored case.
        case: The normalised case as fetched now.
        result: The filter verdict for this revision.
        content_hash: The new revision fingerprint.
        now: Timestamp for ``last_seen_at``. Defaults to now.

    Returns:
        The updated row.

    Raises:
        LookupError: If the case has disappeared between the pre-check and the write.
    """
    moment = now if now is not None else utcnow()
    row = session.get(Case, case_id)
    if row is None:
        message = f"case {case_id} vanished between the deduplication check and the update"
        raise LookupError(message)
    row.revision += 1
    _apply_fields(
        row,
        case,
        content_hash=content_hash,
        court=resolve_court(session, case.jurisdiction_code, case.court),
        now=moment,
    )
    _clear_children(session, row)
    _rebuild_children(row, case, result, moment)
    apply_review(row, result, content_hash=content_hash, now=moment)
    session.flush()
    return row


def _clear_children(session: Session, row: Case) -> None:
    """Delete the child rows this run is about to replace, before the replacements exist.

    A unit of work orders inserts before deletes within one flush, so replacing a collection
    in place would insert the new rows while the old ones are still there. ``citation`` is
    unique on ``(case_id, target_identifier, citation_type)``, and a revision that still
    cites the same instrument — which is to say almost every revision — would violate it and
    take the whole batch down. Emptying the collections and flushing first costs one
    statement per table and makes the replacement mean what it says.

    Args:
        session: Open database session.
        row: The case row whose children are about to be rebuilt.
    """
    row.documents.clear()
    row.parties.clear()
    row.citations.clear()
    row.keyword_matches.clear()
    session.flush()


def persist_case(
    session: Session,
    case: NormalisedCase,
    result: FilterResult,
    *,
    content_hash: str,
    case_id: int | None,
    now: datetime | None = None,
) -> PersistOutcome:
    """Insert or update a case, whichever deduplication concluded.

    Args:
        session: Open database session.
        case: The normalised case.
        result: The filter verdict for it.
        content_hash: Revision fingerprint to store.
        case_id: Primary key of the stored case, or ``None`` for a new one.
        now: Timestamp to write. Defaults to now.

    Returns:
        :attr:`PersistOutcome.INSERTED` or :attr:`PersistOutcome.UPDATED`.
    """
    if case_id is None:
        insert_case(session, case, result, content_hash=content_hash, now=now)
        return PersistOutcome.INSERTED
    update_case(session, case_id, case, result, content_hash=content_hash, now=now)
    return PersistOutcome.UPDATED
