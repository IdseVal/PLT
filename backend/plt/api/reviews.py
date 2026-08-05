"""Review queue endpoints: ``/api/reviews`` and below.

The API side of the selection policy in ``docs/core-document.md`` section 2.7. Selection
admits generously, because a false negative is a case the tracker implicitly claims does not
exist; precision is bought back here, by a content manager confirming or rejecting the cases
that passed inside their list's review band. Two routes are enough for that: list the queue,
and record a verdict on one item.

**The reviewer may be a person or an agent.** That is undecided, so nothing here assumes
either. There is no session, no form and no HTML: a caller authenticates with a bearer token,
reads JSON and writes JSON, and the identity it records for itself (``decided_by``) is an
opaque string. A screen and a scheduled agent are equally first-class clients.

**These endpoints are not public.** The queue lists cases a rejection has unpublished — which
``/api/cases`` deliberately reports as absent — and the decision route can unpublish more, so
the blueprint refuses to serve anything unless ``PLT_REVIEW_API_TOKEN`` is configured, and
then only to a caller presenting it. The token travels in the ``Authorization`` header rather
than in a cookie or a query parameter: it is therefore neither logged as part of the URL nor
attachable by a browser to a cross-site request, which is what makes the state-changing route
safe without a CSRF token of its own.
"""

from __future__ import annotations

import secrets
from functools import wraps
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, TypeVar, cast

from flask import Blueprint, request

from plt.api.errors import ApiError, NotFoundError
from plt.api.schemas import (
    parse_review_decision,
    parse_review_query,
    review_item_payload,
    review_page_payload,
)
from plt.db.repositories import (
    get_review_by_id,
    record_review_decision,
    search_reviews,
)
from plt.extensions import current_settings, db_session, limiter
from plt.utils.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["reviews_bp"]

log = get_logger(__name__)

#: Mounted at ``<api_prefix>/reviews`` by :func:`plt.api.register_blueprints`.
reviews_bp = Blueprint("reviews", __name__)

#: Prefix of the ``Authorization`` header value this API accepts.
_BEARER = "Bearer "

#: Any view function; preserved through the authentication decorator.
_ViewT = TypeVar("_ViewT", bound="Callable[..., Any]")


class ReviewQueueDisabledError(ApiError):
    """No review token is configured, so the queue endpoints serve nothing."""

    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "review_queue_disabled"


class UnauthorisedError(ApiError):
    """The caller presented no token, or the wrong one."""

    status_code = HTTPStatus.UNAUTHORIZED
    code = "unauthorized"


def _review_rate_limit() -> str:
    """Return the configured rate limit for the review endpoints.

    Evaluated per request rather than at import time, so the limit stays configuration and a
    test application can carry a different one.

    Returns:
        A limit expression such as ``30 per minute``.
    """
    return current_settings().rate_limit_review


def _authenticate() -> None:
    """Verify the caller's bearer token against the configured one.

    Raises:
        ReviewQueueDisabledError: If no token is configured. The queue is then closed rather
            than open: an unset secret must never mean "no authentication required".
        UnauthorisedError: If the header is missing, malformed or does not match. The
            comparison is constant-time, so a wrong token cannot be found byte by byte.
    """
    configured = current_settings().review_api_token
    if configured is None:
        message = "The review queue is not configured on this deployment."
        raise ReviewQueueDisabledError(message)

    header = request.headers.get("Authorization", "")
    if not header.startswith(_BEARER):
        message = "A bearer token is required."
        raise UnauthorisedError(message)
    presented = header[len(_BEARER) :].strip()
    if not secrets.compare_digest(presented, configured.get_secret_value()):
        log.warning("review queue request presented an invalid token")
        message = "The bearer token presented is not valid."
        raise UnauthorisedError(message)


def _authenticated(view: _ViewT) -> _ViewT:
    """Require a valid review token before the view runs.

    Args:
        view: The view function to guard.

    Returns:
        The guarded view.
    """

    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - passes a view's own signature through
        _authenticate()
        return view(*args, **kwargs)

    return cast(_ViewT, wrapper)


@reviews_bp.get("")
@limiter.limit(_review_rate_limit)
@_authenticated
def list_reviews() -> tuple[dict[str, Any], int]:
    """List the review queue, filtered and paginated.

    Query parameters: ``status`` (repeatable, ``pending`` by default, ``any`` for every
    status), ``jurisdiction`` (repeatable), ``list_version``, ``decided_by``, ``sort``
    (``flagged_asc`` by default, ``flagged_desc``, ``score_asc``, ``score_desc``), ``page``
    and ``page_size``.

    Returns:
        A ``(payload, status)`` pair carrying one page of the queue, each item complete with
        its case and the matched terms that put it there.
    """
    settings = current_settings()
    query = parse_review_query(request.args, settings)
    page = search_reviews(
        db_session(),
        query.criteria,
        page=query.page,
        page_size=query.page_size,
    )
    return review_page_payload(page), HTTPStatus.OK


@reviews_bp.post("/<int:review_id>/decision")
@limiter.limit(_review_rate_limit)
@_authenticated
def decide(review_id: int) -> tuple[dict[str, Any], int]:
    """Record a confirmation or a rejection on one review item.

    Body: ``{"decision": "confirmed" | "rejected", "decided_by": "...", "note": "..."}``.
    A rejection unpublishes the case without deleting it or its evidence; a confirmation
    republishes a case that a previous rejection had withheld. Both are appended to the
    item's history, so a verdict a later upstream revision supersedes stays on the record.

    Args:
        review_id: Primary key of the review item, from the path.

    Returns:
        A ``(payload, status)`` pair carrying the item as it now stands.

    Raises:
        NotFoundError: If no review item has that identifier.
    """
    body = request.get_json(silent=True)
    decision = parse_review_decision(body)

    session = db_session()
    review = get_review_by_id(session, review_id)
    if review is None:
        message = "No review item exists with that identifier."
        raise NotFoundError(message, details={"review_id": review_id})

    record_review_decision(
        session,
        review,
        decision=decision.decision,
        decided_by=decision.decided_by,
        note=decision.note,
    )
    session.commit()
    log.info(
        "review decision recorded",
        extra={
            "context": {
                "review_id": review.id,
                "case_id": review.case_id,
                "decision": str(decision.decision.value),
                "published": review.case.is_published,
            }
        },
    )
    return review_item_payload(review), HTTPStatus.OK
