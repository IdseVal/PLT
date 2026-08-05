"""Query helpers used by the API and pipeline layers.

Repositories keep SQL out of the HTTP layer: the API calls typed functions here, and every
query is built with SQLAlchemy Core constructs, so user input always travels as a bound
parameter and never as string-interpolated SQL (``docs/architecture.md`` section 5). The
one place user text reaches an operator is :func:`like_pattern`, which escapes the ``LIKE``
wildcards before the value is bound.

Policy lives in the caller. These functions validate their arguments defensively but do not
read :class:`plt.config.Settings`: page sizes, limits and their maxima are clamped by the
API layer from configuration, which keeps the same helpers usable by the pipeline and the
CLI.
"""

from __future__ import annotations

import enum
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Select, SQLColumnExpression, and_, func, or_, select
from sqlalchemy import case as sql_case
from sqlalchemy.orm import selectinload

from plt.db.base import utcnow
from plt.db.models import (
    Case,
    CaseDocument,
    CaseReview,
    CaseReviewDecision,
    CaseTopic,
    Court,
    IngestRun,
    IngestStatus,
    Jurisdiction,
    JurisdictionType,
    LawDomain,
    ReviewDecision,
    ReviewStatus,
    Subscriber,
    SubscriberStatus,
    Topic,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from sqlalchemy.sql.elements import ColumnElement

__all__ = [
    "CaseFingerprint",
    "CasePage",
    "CaseSearchCriteria",
    "CaseSort",
    "FacetValues",
    "IngestRunSummary",
    "JurisdictionStat",
    "ReviewPage",
    "ReviewSearchCriteria",
    "ReviewSort",
    "cases_first_seen",
    "confirmed_subscribers_after",
    "count_cases",
    "count_cases_first_seen",
    "count_reviews",
    "count_reviews_flagged_since",
    "get_case_by_id",
    "get_case_by_source_id",
    "get_case_fingerprint",
    "get_review_by_id",
    "get_subscriber_by_email",
    "get_subscriber_by_token_seed",
    "jurisdiction_stats",
    "latest_cases",
    "latest_successful_runs",
    "like_pattern",
    "list_facets",
    "record_review_decision",
    "reviews_flagged_since",
    "search_cases",
    "search_reviews",
    "stream_cases",
]

#: Escape character used with ``LIKE``/``ILIKE``; must be escaped in the pattern itself.
_LIKE_ESCAPE = "\\"


class CaseSort(enum.StrEnum):
    """Ordering accepted by the case list endpoints (architecture section 5)."""

    DATE_DESC = "date_desc"
    DATE_ASC = "date_asc"
    RELEVANCE = "relevance"


class ReviewSort(enum.StrEnum):
    """Ordering accepted by the review queue endpoint.

    ``flagged_asc`` is the default because a queue is worked oldest first; the score
    orderings exist because the band's lower edge is where the false positives concentrate,
    so a reviewer with limited time starts there.
    """

    FLAGGED_ASC = "flagged_asc"
    FLAGGED_DESC = "flagged_desc"
    SCORE_ASC = "score_asc"
    SCORE_DESC = "score_desc"


def like_pattern(term: str) -> str:
    r"""Turn a user-supplied term into a safe ``LIKE`` containment pattern.

    The value is still bound as a parameter; escaping only stops a user from turning their
    own search term into a wildcard, which would otherwise let a single ``%`` scan the whole
    corpus.

    Args:
        term: Raw search text from the client.

    Returns:
        A pattern of the form ``%term%`` with ``\\``, ``%`` and ``_`` escaped.
    """
    escaped = (
        term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )
    return f"%{escaped}%"


@dataclass(frozen=True, slots=True)
class CaseSearchCriteria:
    """Filters accepted by :func:`search_cases`, :func:`count_cases` and :func:`stream_cases`.

    Every field is optional; the default instance selects all published cases, newest
    decision first. The field names mirror the query parameters in architecture section 5.
    """

    query: str | None = None
    jurisdictions: tuple[str, ...] = ()
    law_domain: LawDomain | None = None
    law_subfield: str | None = None
    topic_slug: str | None = None
    court_id: int | None = None
    language: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    sort: CaseSort = CaseSort.DATE_DESC
    #: Editorial view only: unpublished cases are never exposed by the public API.
    include_unpublished: bool = False


class _Paginated:
    """The arithmetic every paginated result shares.

    Split out so the case list and the review queue cannot drift into two different answers
    for "how many pages is that", which the API contract fixes: an empty result set is one
    empty page, never zero pages (``docs/architecture.md`` section 5.1).
    """

    __slots__ = ()

    total: int
    page: int
    page_size: int

    @property
    def page_count(self) -> int:
        """Return the number of pages the current page size yields."""
        return max(1, math.ceil(self.total / self.page_size))

    @property
    def has_next(self) -> bool:
        """Return whether a further page exists after this one."""
        return self.page < self.page_count


@dataclass(frozen=True, slots=True)
class CasePage(_Paginated):
    """One page of search results, with the totals a paginator needs."""

    items: tuple[Case, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class ReviewPage(_Paginated):
    """One page of the review queue, with the totals a paginator needs."""

    items: tuple[CaseReview, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class ReviewSearchCriteria:
    """Filters accepted by :func:`search_reviews` and :func:`count_reviews`.

    The default instance selects the queue itself: everything still awaiting a decision,
    oldest flag first.
    """

    statuses: tuple[ReviewStatus, ...] = (ReviewStatus.PENDING,)
    jurisdictions: tuple[str, ...] = ()
    #: Version of the keyword list that produced the flag, so a curator can review the
    #: effect of one revision of a list rather than the whole backlog.
    list_version: str | None = None
    #: Identifier of the person or agent that took the standing decision.
    decided_by: str | None = None
    sort: ReviewSort = ReviewSort.FLAGGED_ASC


@dataclass(frozen=True, slots=True)
class CaseFingerprint:
    """The minimum a deduplication pre-check needs about a known case.

    Loading this instead of the full row keeps the pipeline's "have we seen this, and has it
    changed?" check cheap enough to run for every candidate (core document section 2.6).
    """

    id: int
    content_hash: str | None
    revision: int
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class JurisdictionStat:
    """One entry of the map payload served by ``/api/stats/jurisdictions``."""

    code: str
    name: str
    type: JurisdictionType
    map_feature_id: str | None
    is_active: bool
    case_count: int
    latest_decision_date: date | None


@dataclass(frozen=True, slots=True)
class FacetValues:
    """Distinct values behind the All-cases filter UI (``/api/filters``)."""

    jurisdictions: tuple[tuple[str, str], ...] = ()
    courts: tuple[tuple[int, str], ...] = ()
    law_domains: tuple[str, ...] = ()
    law_subfields: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    topics: tuple[tuple[str, str], ...] = ()
    earliest_decision_date: date | None = None
    latest_decision_date: date | None = None


@dataclass(frozen=True, slots=True)
class IngestRunSummary:
    """The last successful ingestion per jurisdiction, for ``/api/health``."""

    jurisdiction_code: str
    connector: str
    finished_at: datetime | None
    inserted_count: int
    updated_count: int


#: Relationships loaded eagerly for a review listing. The matched terms are among them on
#: purpose: a reviewer decides on the evidence, and the contract requires it to travel with
#: the item rather than costing a second request per row (architecture section 5.1).
_REVIEW_OPTIONS = (
    selectinload(CaseReview.case).selectinload(Case.keyword_matches),
    selectinload(CaseReview.case).joinedload(Case.jurisdiction),
    selectinload(CaseReview.case).joinedload(Case.court),
    selectinload(CaseReview.decisions),
)

#: Relationships loaded eagerly when a caller asks for a case with its details.
_DETAIL_OPTIONS = (
    selectinload(Case.documents),
    selectinload(Case.parties),
    selectinload(Case.topics),
    selectinload(Case.keyword_matches),
    selectinload(Case.citations),
)


def _conditions(criteria: CaseSearchCriteria) -> list[ColumnElement[bool]]:
    """Translate search criteria into a list of SQL predicates.

    Args:
        criteria: The filters to apply.

    Returns:
        Predicates to be ``AND``-ed onto a ``case`` selection. Text filters use an escaped,
        bound ``LIKE`` pattern; the topic filter compiles to an ``EXISTS`` so no join has to
        be added by the caller.
    """
    clauses: list[ColumnElement[bool]] = []

    if not criteria.include_unpublished:
        clauses.append(Case.is_published.is_(True))
    if criteria.jurisdictions:
        clauses.append(Case.jurisdiction_code.in_(criteria.jurisdictions))
    if criteria.law_domain is not None:
        clauses.append(Case.law_domain == criteria.law_domain)
    if criteria.law_subfield:
        clauses.append(Case.law_subfield == criteria.law_subfield)
    if criteria.language:
        clauses.append(Case.language == criteria.language)
    if criteria.court_id is not None:
        clauses.append(Case.court_id == criteria.court_id)
    if criteria.topic_slug:
        # Two nested EXISTS rather than a join: ``Case.topics`` is the association object,
        # so the topic itself has to be reached through it.
        clauses.append(Case.topics.any(CaseTopic.topic.has(Topic.slug == criteria.topic_slug)))
    if criteria.date_from is not None:
        clauses.append(Case.decision_date >= criteria.date_from)
    if criteria.date_to is not None:
        clauses.append(Case.decision_date <= criteria.date_to)

    term = (criteria.query or "").strip()
    if term:
        pattern = like_pattern(term)
        clauses.append(
            or_(
                Case.title.ilike(pattern, escape=_LIKE_ESCAPE),
                Case.abstract.ilike(pattern, escape=_LIKE_ESCAPE),
                Case.source_id.ilike(pattern, escape=_LIKE_ESCAPE),
                Case.documents.any(
                    CaseDocument.full_text.ilike(pattern, escape=_LIKE_ESCAPE),
                ),
            )
        )
    return clauses


def _ordering(criteria: CaseSearchCriteria) -> tuple[SQLColumnExpression[Any], ...]:
    """Return the ``ORDER BY`` expressions for a set of criteria.

    ``relevance`` has no full-text index behind it — the schema stays portable between
    SQLite and PostgreSQL — so it ranks a title hit above an abstract hit above a full-text
    hit, and falls back to the newest decision within a rank. Without a query term it is
    identical to ``date_desc``.

    Args:
        criteria: The search criteria, including the requested sort.

    Returns:
        Expressions to pass to ``order_by``. ``id`` is appended as a tie-breaker so that
        paging over equal dates is stable.
    """
    term = (criteria.query or "").strip()
    if criteria.sort is CaseSort.DATE_ASC:
        return (Case.decision_date.asc().nullslast(), Case.id.asc())
    if criteria.sort is CaseSort.RELEVANCE and term:
        pattern = like_pattern(term)
        rank = sql_case(
            (Case.title.ilike(pattern, escape=_LIKE_ESCAPE), 0),
            (Case.abstract.ilike(pattern, escape=_LIKE_ESCAPE), 1),
            else_=2,
        )
        return (rank.asc(), Case.decision_date.desc().nullslast(), Case.id.desc())
    return (Case.decision_date.desc().nullslast(), Case.id.desc())


def _select_cases(criteria: CaseSearchCriteria) -> Select[tuple[Case]]:
    """Build the filtered, ordered selection of cases for a set of criteria.

    Args:
        criteria: The filters and ordering to apply.

    Returns:
        A ``SELECT`` over :class:`~plt.db.models.Case` without pagination applied.
    """
    return select(Case).where(*_conditions(criteria)).order_by(*_ordering(criteria))


def count_cases(session: Session, criteria: CaseSearchCriteria | None = None) -> int:
    """Count the cases matching the criteria.

    Args:
        session: Open database session.
        criteria: Filters to apply. Defaults to all published cases.

    Returns:
        The number of matching cases.
    """
    criteria = criteria or CaseSearchCriteria()
    stmt = select(func.count(Case.id)).where(*_conditions(criteria))
    return session.execute(stmt).scalar_one()


def search_cases(
    session: Session,
    criteria: CaseSearchCriteria | None = None,
    *,
    page: int = 1,
    page_size: int = 20,
    with_details: bool = False,
) -> CasePage:
    """Return one page of cases matching the criteria, plus the total.

    Args:
        session: Open database session.
        criteria: Filters and ordering. Defaults to all published cases, newest first.
        page: 1-based page number.
        page_size: Rows per page. The caller is responsible for clamping this to the
            configured maximum; the bound here only rejects nonsense.
        with_details: Eagerly load documents, parties, topics, matches and citations.

    Returns:
        A :class:`CasePage`.

    Raises:
        ValueError: If ``page`` or ``page_size`` is below 1.
    """
    if page < 1:
        message = f"page must be 1 or greater, got {page}"
        raise ValueError(message)
    if page_size < 1:
        message = f"page_size must be 1 or greater, got {page_size}"
        raise ValueError(message)

    criteria = criteria or CaseSearchCriteria()
    stmt = _select_cases(criteria).offset((page - 1) * page_size).limit(page_size)
    if with_details:
        stmt = stmt.options(*_DETAIL_OPTIONS)

    items: Sequence[Case] = session.scalars(stmt).unique().all()
    return CasePage(
        items=tuple(items),
        total=count_cases(session, criteria),
        page=page,
        page_size=page_size,
    )


def stream_cases(
    session: Session,
    criteria: CaseSearchCriteria | None = None,
    *,
    batch_size: int = 200,
) -> Iterator[Case]:
    """Yield matching cases in batches, for the export endpoint and the CLI.

    The result set is fetched ``batch_size`` rows at a time rather than materialised in
    full, so exporting the whole corpus keeps memory flat (architecture rule 2.3).

    Args:
        session: Open database session.
        criteria: Filters and ordering. Defaults to all published cases.
        batch_size: Rows fetched per round trip.

    Yields:
        Matching :class:`~plt.db.models.Case` rows, in the requested order.

    Raises:
        ValueError: If ``batch_size`` is below 1.
    """
    if batch_size < 1:
        message = f"batch_size must be 1 or greater, got {batch_size}"
        raise ValueError(message)

    criteria = criteria or CaseSearchCriteria()
    stmt = _select_cases(criteria).execution_options(yield_per=batch_size)
    yield from session.scalars(stmt)


def latest_cases(session: Session, limit: int = 20) -> tuple[Case, ...]:
    """Return the most recently decided published cases, newest first.

    Feeds the home-page sidebar (``/api/cases/latest``). Cases without a decision date sort
    last so an incomplete record cannot occupy the top of the feed.

    Args:
        session: Open database session.
        limit: Maximum number of cases. Clamped by the caller from configuration.

    Returns:
        Up to ``limit`` cases.

    Raises:
        ValueError: If ``limit`` is below 1.
    """
    if limit < 1:
        message = f"limit must be 1 or greater, got {limit}"
        raise ValueError(message)

    stmt = (
        select(Case)
        .where(Case.is_published.is_(True))
        .order_by(Case.decision_date.desc().nullslast(), Case.id.desc())
        .limit(limit)
    )
    return tuple(session.scalars(stmt).all())


def get_case_by_id(session: Session, case_id: int, *, with_details: bool = False) -> Case | None:
    """Look a case up by primary key.

    Args:
        session: Open database session.
        case_id: Primary key.
        with_details: Eagerly load documents, parties, topics, matches and citations.

    Returns:
        The case, or ``None`` if there is no such row.
    """
    stmt = select(Case).where(Case.id == case_id)
    if with_details:
        stmt = stmt.options(*_DETAIL_OPTIONS)
    return session.scalars(stmt).unique().one_or_none()


def get_case_by_source_id(
    session: Session,
    jurisdiction_code: str,
    source_id: str,
    *,
    with_details: bool = False,
) -> Case | None:
    """Look a case up by its source identifier — the deduplication key.

    This is the query behind ``/api/cases/<jurisdiction>/<source_id>`` and behind the
    pipeline's duplicate check, and it is backed by the unique constraint on
    ``(jurisdiction_code, source_id)``.

    Args:
        session: Open database session.
        jurisdiction_code: Jurisdiction code, e.g. ``NL`` or ``EU``.
        source_id: ECLI (NL) or CELEX (EU) identifier.
        with_details: Eagerly load documents, parties, topics, matches and citations.

    Returns:
        The case, or ``None`` if it has not been ingested.
    """
    stmt = select(Case).where(
        Case.jurisdiction_code == jurisdiction_code,
        Case.source_id == source_id,
    )
    if with_details:
        stmt = stmt.options(*_DETAIL_OPTIONS)
    return session.scalars(stmt).unique().one_or_none()


def get_case_fingerprint(
    session: Session, jurisdiction_code: str, source_id: str
) -> CaseFingerprint | None:
    """Return just the columns the deduplication pre-check needs.

    Args:
        session: Open database session.
        jurisdiction_code: Jurisdiction code, e.g. ``NL`` or ``EU``.
        source_id: ECLI (NL) or CELEX (EU) identifier.

    Returns:
        A :class:`CaseFingerprint`, or ``None`` if the case is unknown.
    """
    stmt = select(Case.id, Case.content_hash, Case.revision, Case.last_seen_at).where(
        Case.jurisdiction_code == jurisdiction_code,
        Case.source_id == source_id,
    )
    row = session.execute(stmt).one_or_none()
    if row is None:
        return None
    return CaseFingerprint(
        id=row.id,
        content_hash=row.content_hash,
        revision=row.revision,
        last_seen_at=row.last_seen_at,
    )


def _review_conditions(criteria: ReviewSearchCriteria) -> list[ColumnElement[bool]]:
    """Translate review-queue criteria into a list of SQL predicates.

    Args:
        criteria: The filters to apply.

    Returns:
        Predicates to be ``AND``-ed onto a ``case_review`` selection. The jurisdiction filter
        compiles to an ``EXISTS`` against ``case`` so the caller adds no join.
    """
    clauses: list[ColumnElement[bool]] = []
    if criteria.statuses:
        clauses.append(CaseReview.status.in_(criteria.statuses))
    if criteria.jurisdictions:
        clauses.append(CaseReview.case.has(Case.jurisdiction_code.in_(criteria.jurisdictions)))
    if criteria.list_version:
        clauses.append(CaseReview.list_version == criteria.list_version)
    if criteria.decided_by:
        clauses.append(CaseReview.decided_by == criteria.decided_by)
    return clauses


def _review_ordering(criteria: ReviewSearchCriteria) -> tuple[SQLColumnExpression[Any], ...]:
    """Return the ``ORDER BY`` expressions for a review-queue selection.

    Args:
        criteria: The criteria, including the requested sort.

    Returns:
        Expressions to pass to ``order_by``, with ``id`` appended as a tie-breaker so paging
        over equal timestamps or equal scores is stable.
    """
    if criteria.sort is ReviewSort.FLAGGED_DESC:
        return (CaseReview.flagged_at.desc(), CaseReview.id.desc())
    if criteria.sort is ReviewSort.SCORE_ASC:
        return (CaseReview.score.asc().nullsfirst(), CaseReview.id.asc())
    if criteria.sort is ReviewSort.SCORE_DESC:
        return (CaseReview.score.desc().nullslast(), CaseReview.id.desc())
    return (CaseReview.flagged_at.asc(), CaseReview.id.asc())


def count_reviews(session: Session, criteria: ReviewSearchCriteria | None = None) -> int:
    """Count the review items matching the criteria.

    Args:
        session: Open database session.
        criteria: Filters to apply. Defaults to the pending queue.

    Returns:
        The number of matching items.
    """
    criteria = criteria or ReviewSearchCriteria()
    stmt = select(func.count(CaseReview.id)).where(*_review_conditions(criteria))
    return session.execute(stmt).scalar_one()


def search_reviews(
    session: Session,
    criteria: ReviewSearchCriteria | None = None,
    *,
    page: int = 1,
    page_size: int = 20,
) -> ReviewPage:
    """Return one page of the review queue, plus the total.

    Each item arrives with its case, that case's keyword matches and the decisions taken on
    it, so a reviewer — human or agent — has the evidence in hand without a second request.

    Args:
        session: Open database session.
        criteria: Filters and ordering. Defaults to the pending queue, oldest flag first.
        page: 1-based page number.
        page_size: Rows per page. The caller clamps this from configuration.

    Returns:
        A :class:`ReviewPage`.

    Raises:
        ValueError: If ``page`` or ``page_size`` is below 1.
    """
    if page < 1:
        message = f"page must be 1 or greater, got {page}"
        raise ValueError(message)
    if page_size < 1:
        message = f"page_size must be 1 or greater, got {page_size}"
        raise ValueError(message)

    criteria = criteria or ReviewSearchCriteria()
    stmt = (
        select(CaseReview)
        .where(*_review_conditions(criteria))
        .order_by(*_review_ordering(criteria))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .options(*_REVIEW_OPTIONS)
    )
    items: Sequence[CaseReview] = session.scalars(stmt).unique().all()
    return ReviewPage(
        items=tuple(items),
        total=count_reviews(session, criteria),
        page=page,
        page_size=page_size,
    )


def get_review_by_id(session: Session, review_id: int) -> CaseReview | None:
    """Look one review item up by primary key, with its case and its evidence.

    Args:
        session: Open database session.
        review_id: Primary key of the review item.

    Returns:
        The item, or ``None`` if there is no such row.
    """
    stmt = select(CaseReview).where(CaseReview.id == review_id).options(*_REVIEW_OPTIONS)
    return session.scalars(stmt).unique().one_or_none()


def record_review_decision(
    session: Session,
    review: CaseReview,
    *,
    decision: ReviewDecision,
    decided_by: str,
    note: str | None = None,
    now: datetime | None = None,
) -> CaseReviewDecision:
    """Record a content manager's verdict on a flagged case.

    Two rows are written, and the difference between them is the point. The decision is
    appended to ``case_review_decision``, which is never rewritten, so a verdict later
    superseded by an upstream revision is still on the record. The same verdict is copied
    onto the ``case_review`` row as the *standing* decision, which is what the queue lists
    and what governs publication.

    The case's ``needs_review`` flag is deliberately **not** cleared. It states what the
    filter concluded about the text, and re-running the same window over the same corpus has
    to produce the same flags whether or not anyone has decided in the meantime
    (``docs/core-document.md`` section 2.8). The workflow lives in ``case_review.status``.

    Args:
        session: Open database session.
        review: The item being decided, with its case loaded.
        decision: Confirm or reject.
        decided_by: Identifier of the person or agent deciding. Recorded verbatim.
        note: Optional rationale.
        now: Timestamp to write. Defaults to the current UTC instant.

    Returns:
        The appended history row.
    """
    moment = now if now is not None else utcnow()
    case = review.case

    review.decision = decision
    review.decided_by = decided_by
    review.decided_at = moment
    review.decided_revision = case.revision
    review.decision_note = note
    review.status = (
        ReviewStatus.CONFIRMED if decision is ReviewDecision.CONFIRMED else ReviewStatus.REJECTED
    )
    review.apply_publication(case)

    entry = CaseReviewDecision(
        decision=decision,
        decided_by=decided_by,
        decided_at=moment,
        note=note,
        case_revision=case.revision,
        content_hash=case.content_hash,
    )
    review.decisions.append(entry)
    session.flush()
    return entry


def jurisdiction_stats(session: Session) -> tuple[JurisdictionStat, ...]:
    """Return the map payload: every jurisdiction with its published case count.

    One query for all jurisdictions, as the frontend map requires (architecture section 5).
    The join is an outer join on purpose: a jurisdiction with no cases yet must still be
    returned, so the map can render it in its muted "no cases yet" state instead of dropping
    the shape.

    Args:
        session: Open database session.

    Returns:
        One :class:`JurisdictionStat` per jurisdiction, ordered by code.
    """
    stmt = (
        select(
            Jurisdiction.code,
            Jurisdiction.name,
            Jurisdiction.type,
            Jurisdiction.map_feature_id,
            Jurisdiction.is_active,
            func.count(Case.id).label("case_count"),
            func.max(Case.decision_date).label("latest_decision_date"),
        )
        .select_from(Jurisdiction)
        .outerjoin(
            Case,
            and_(Case.jurisdiction_code == Jurisdiction.code, Case.is_published.is_(True)),
        )
        .group_by(
            Jurisdiction.code,
            Jurisdiction.name,
            Jurisdiction.type,
            Jurisdiction.map_feature_id,
            Jurisdiction.is_active,
        )
        .order_by(Jurisdiction.code)
    )
    return tuple(
        JurisdictionStat(
            code=row.code,
            name=row.name,
            type=row.type,
            map_feature_id=row.map_feature_id,
            is_active=row.is_active,
            case_count=row.case_count,
            latest_decision_date=row.latest_decision_date,
        )
        for row in session.execute(stmt)
    )


def _distinct_strings(session: Session, column: SQLColumnExpression[Any]) -> tuple[str, ...]:
    """Return the distinct non-null values of a text column of published cases.

    Args:
        session: Open database session.
        column: The column to collect values from. Enumerated columns are rendered as their
            string value, which is the form the API exposes.

    Returns:
        The distinct values, in database order.
    """
    stmt = (
        select(column)
        .where(Case.is_published.is_(True), column.is_not(None))
        .distinct()
        .order_by(column)
    )
    return tuple(str(value) for value in session.scalars(stmt) if value is not None)


def list_facets(session: Session) -> FacetValues:
    """Return the facet values behind the All-cases filter UI.

    Only values that actually occur on a published case are returned, so the UI cannot offer
    a filter that yields nothing.

    Args:
        session: Open database session.

    Returns:
        The populated :class:`FacetValues`.
    """
    jurisdictions = session.execute(
        select(Jurisdiction.code, Jurisdiction.name)
        .where(Jurisdiction.cases.any(Case.is_published.is_(True)))
        .order_by(Jurisdiction.code)
    ).all()
    courts = session.execute(
        select(Court.id, Court.name)
        .where(Court.cases.any(Case.is_published.is_(True)))
        .order_by(Court.name)
    ).all()
    topics = session.execute(
        select(Topic.slug, Topic.label)
        .where(Topic.cases.any(CaseTopic.case.has(Case.is_published.is_(True))))
        .order_by(Topic.label)
    ).all()
    date_bounds = session.execute(
        select(func.min(Case.decision_date), func.max(Case.decision_date)).where(
            Case.is_published.is_(True)
        )
    ).one()

    return FacetValues(
        jurisdictions=tuple((row.code, row.name) for row in jurisdictions),
        courts=tuple((row.id, row.name) for row in courts),
        law_domains=_distinct_strings(session, Case.law_domain),
        law_subfields=_distinct_strings(session, Case.law_subfield),
        languages=_distinct_strings(session, Case.language),
        topics=tuple((row.slug, row.label) for row in topics),
        earliest_decision_date=date_bounds[0],
        latest_decision_date=date_bounds[1],
    )


def latest_successful_runs(session: Session) -> tuple[IngestRunSummary, ...]:
    """Return the last successful ingestion run per jurisdiction.

    ``/api/health`` reports these so an operator can see at a glance that the weekly job is
    still landing data for every jurisdiction.

    Args:
        session: Open database session.

    Returns:
        One summary per jurisdiction that has ever completed a run, ordered by code.
    """
    latest = (
        select(
            IngestRun.jurisdiction_code.label("jurisdiction_code"),
            func.max(IngestRun.finished_at).label("finished_at"),
        )
        .where(IngestRun.status == IngestStatus.SUCCESS, IngestRun.finished_at.is_not(None))
        .group_by(IngestRun.jurisdiction_code)
        .subquery()
    )
    stmt = (
        select(IngestRun)
        .join(
            latest,
            and_(
                IngestRun.jurisdiction_code == latest.c.jurisdiction_code,
                IngestRun.finished_at == latest.c.finished_at,
            ),
        )
        .order_by(IngestRun.jurisdiction_code)
    )
    return tuple(
        IngestRunSummary(
            jurisdiction_code=run.jurisdiction_code,
            connector=run.connector,
            finished_at=run.finished_at,
            inserted_count=run.inserted_count,
            updated_count=run.updated_count,
        )
        for run in session.scalars(stmt)
    )


def cases_first_seen(
    session: Session,
    *,
    since: datetime,
    until: datetime,
    limit: int,
) -> tuple[Case, ...]:
    """Return the published cases the tracker first saw inside a window.

    This is what a digest lists: cases *new to the tracker*, not cases decided recently. A
    judgment handed down in 2019 that the pipeline reached this week is news to a subscriber,
    and one delivered yesterday that the tracker has held for a month is not.

    Args:
        session: Open database session.
        since: Inclusive lower bound on ``first_seen_at``.
        until: Exclusive upper bound.
        limit: Most rows to return. The caller bounds it from configuration.

    Returns:
        Up to ``limit`` cases, newest decision first, with their court loaded so rendering a
        listing costs no further query.

    Raises:
        ValueError: If ``limit`` is below 1.
    """
    if limit < 1:
        message = f"limit must be 1 or greater, got {limit}"
        raise ValueError(message)
    stmt = (
        select(Case)
        .options(selectinload(Case.court))
        .where(
            Case.is_published.is_(True),
            Case.first_seen_at >= since,
            Case.first_seen_at < until,
        )
        .order_by(Case.decision_date.desc().nullslast(), Case.id.desc())
        .limit(limit)
    )
    return tuple(session.scalars(stmt).all())


def count_cases_first_seen(session: Session, *, since: datetime, until: datetime) -> int:
    """Count the published cases first seen inside a window.

    Args:
        session: Open database session.
        since: Inclusive lower bound on ``first_seen_at``.
        until: Exclusive upper bound.

    Returns:
        The number of cases, which may exceed what :func:`cases_first_seen` returns.
    """
    stmt = (
        select(func.count())
        .select_from(Case)
        .where(
            Case.is_published.is_(True),
            Case.first_seen_at >= since,
            Case.first_seen_at < until,
        )
    )
    return int(session.scalar(stmt) or 0)


def reviews_flagged_since(
    session: Session,
    *,
    since: datetime,
    limit: int,
) -> tuple[CaseReview, ...]:
    """Return review items flagged at or after an instant and still awaiting a verdict.

    Feeds the administrator's notification (core document section 2.7). Only ``pending`` items
    are reported: an item a content manager has already decided on is not news, and one that
    was withdrawn needs nobody's attention.

    Args:
        session: Open database session.
        since: Inclusive lower bound on ``flagged_at``.
        limit: Most rows to return. The caller bounds it from configuration.

    Returns:
        Up to ``limit`` items, oldest flag first, with their case and court loaded.

    Raises:
        ValueError: If ``limit`` is below 1.
    """
    if limit < 1:
        message = f"limit must be 1 or greater, got {limit}"
        raise ValueError(message)
    stmt = (
        select(CaseReview)
        .options(selectinload(CaseReview.case).selectinload(Case.court))
        .where(CaseReview.status == ReviewStatus.PENDING, CaseReview.flagged_at >= since)
        .order_by(CaseReview.flagged_at, CaseReview.id)
        .limit(limit)
    )
    return tuple(session.scalars(stmt).all())


def count_reviews_flagged_since(session: Session, *, since: datetime) -> int:
    """Count the pending review items flagged at or after an instant.

    Args:
        session: Open database session.
        since: Inclusive lower bound on ``flagged_at``.

    Returns:
        The number of items, which may exceed what :func:`reviews_flagged_since` returns.
    """
    stmt = (
        select(func.count())
        .select_from(CaseReview)
        .where(CaseReview.status == ReviewStatus.PENDING, CaseReview.flagged_at >= since)
    )
    return int(session.scalar(stmt) or 0)


def get_subscriber_by_email(session: Session, email: str) -> Subscriber | None:
    """Look one address up.

    The **only** way this table is read by address, and it answers a caller that already knows
    the address it is asking about. Nothing here lists the table, and the callers are written
    so that a hit and a miss are indistinguishable from outside: an endpoint that behaved
    differently for a known address would be an address-checking oracle.

    Args:
        session: Open database session.
        email: The address, already normalised to lower case by the API layer.

    Returns:
        The row, or ``None``.
    """
    return session.scalars(select(Subscriber).where(Subscriber.email == email)).one_or_none()


def get_subscriber_by_token_seed(session: Session, seed: str) -> Subscriber | None:
    """Look a subscriber up by the selector half of a token.

    Called only after :func:`plt.notifications.tokens.verify_token` has accepted the token, so
    an unauthenticated caller cannot reach this with a seed of their own choosing.

    Args:
        session: Open database session.
        seed: The verified selector.

    Returns:
        The row, or ``None`` when the subscription has since been removed.
    """
    return session.scalars(select(Subscriber).where(Subscriber.token_seed == seed)).one_or_none()


def confirmed_subscribers_after(
    session: Session,
    *,
    after_id: int,
    limit: int,
    not_sent_since: datetime | None = None,
) -> tuple[Subscriber, ...]:
    """Return one batch of confirmed subscribers, for the digest send.

    Keyset pagination on the primary key rather than ``OFFSET``: the send commits after every
    batch, so an offset would shift under a row that changed in the meantime. Combined with
    ``not_sent_since`` it is what makes an interrupted digest resumable — a re-run skips the
    recipients the previous attempt already served, and nobody is sent the same digest twice.

    Args:
        session: Open database session.
        after_id: Exclusive lower bound on the primary key; ``0`` starts at the beginning.
        limit: Batch size.
        not_sent_since: When given, only subscribers whose ``last_digest_at`` is unset or
            older than this instant.

    Returns:
        Up to ``limit`` confirmed subscribers, in primary-key order.

    Raises:
        ValueError: If ``limit`` is below 1.
    """
    if limit < 1:
        message = f"limit must be 1 or greater, got {limit}"
        raise ValueError(message)
    stmt = (
        select(Subscriber)
        .where(Subscriber.status == SubscriberStatus.CONFIRMED, Subscriber.id > after_id)
        .order_by(Subscriber.id)
        .limit(limit)
    )
    if not_sent_since is not None:
        stmt = stmt.where(
            or_(
                Subscriber.last_digest_at.is_(None),
                Subscriber.last_digest_at < not_sent_since,
            )
        )
    return tuple(session.scalars(stmt).all())
