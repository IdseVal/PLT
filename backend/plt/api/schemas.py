"""Request validation and response serialisation for the HTTP API.

Two responsibilities, both fixed by ``docs/architecture.md`` section 5.

**Validation.** Every query parameter is parsed and bounded here before it reaches a
repository helper: an unparsable value, an unknown enumeration member or a value outside
its range is rejected with a ``validation_error`` envelope rather than silently coerced.
Numeric bounds come from :class:`plt.config.Settings` (``page_size_default``,
``page_size_max``, ``latest_limit_default``, ``latest_limit_max``) so they are never
literals in a route. The remaining constants below bound the *length* of free-text input;
they are input-validation guards against a pathological request, not tunable policy, which
is why they live with the parser rather than in settings.

**Serialisation.** The payload builders turn ORM rows into the wire format the frontend's
``src/api/client.ts`` consumes. They are the only place a database column becomes a JSON
key, so a column that must not be exposed - ``case_document.raw_payload``, the editorial
``is_published`` switch - is excluded in exactly one place.

Nothing here builds SQL. User input leaves this module as a
:class:`~plt.db.repositories.CaseSearchCriteria`, and the repository layer binds it as a
parameter; that seam is also what lets a PostgreSQL full-text implementation replace the
current portable ``LIKE`` search without the API layer changing.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Final, TypeVar, overload

from plt.api.errors import ValidationError
from plt.db.models import (
    Case,
    CaseDocument,
    CaseReview,
    CaseReviewDecision,
    CaseTopic,
    Citation,
    KeywordMatch,
    LawDomain,
    Party,
    ReviewDecision,
    ReviewStatus,
)
from plt.db.repositories import (
    CasePage,
    CaseSearchCriteria,
    CaseSort,
    FacetValues,
    IngestRunSummary,
    JurisdictionStat,
    ReviewPage,
    ReviewSearchCriteria,
    ReviewSort,
)
from plt.notifications.pseudonyms import normalise_address
from plt.pipeline.filters.keywords import (
    KeywordListError,
    load_excluded_terms_for,
    load_keyword_list_for,
)
from plt.utils.logging import get_logger

log = get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from werkzeug.datastructures import MultiDict

    from plt.config import Settings

__all__ = [
    "EXPORT_COLUMNS",
    "CaseQuery",
    "ExportFormat",
    "ReviewDecisionInput",
    "ReviewQuery",
    "case_detail_payload",
    "case_summary_payload",
    "csv_cell",
    "csv_record",
    "exclusions_payload",
    "export_metadata",
    "export_row",
    "facets_payload",
    "health_ingest_payload",
    "jurisdiction_stat_payload",
    "page_payload",
    "parse_case_query",
    "parse_export_format",
    "parse_export_query",
    "parse_latest_limit",
    "parse_review_decision",
    "parse_review_query",
    "parse_source_id",
    "parse_subscription_request",
    "parse_subscription_token",
    "review_item_payload",
    "review_page_payload",
    "validate_jurisdiction_code",
]

#: Longest accepted free-text search term. A ``LIKE`` scan is linear in the corpus, so the
#: request that triggers it is bounded rather than left to the client.
_MAX_QUERY_LENGTH: Final[int] = 200

#: Longest accepted value for a filter matched against a column, e.g. ``law_subfield``.
_MAX_FILTER_LENGTH: Final[int] = 255

#: Longest accepted jurisdiction code; matches ``jurisdiction.code`` in the schema.
_MAX_CODE_LENGTH: Final[int] = 8

#: Longest accepted source identifier; matches ``case.source_id`` in the schema.
_MAX_SOURCE_ID_LENGTH: Final[int] = 255

#: Most jurisdictions accepted in one request. The map offers a handful; a client asking
#: for hundreds is building a pathological ``IN`` list.
_MAX_JURISDICTIONS: Final[int] = 32

#: Longest echoed back in an error message, so a huge value cannot inflate the response.
_MAX_ECHO_LENGTH: Final[int] = 100

#: Longest accepted reviewer identifier; matches ``case_review.decided_by`` in the schema.
_MAX_DECIDED_BY_LENGTH: Final[int] = 255

#: Longest accepted rationale on a review decision. The column is unbounded ``TEXT``; the
#: bound is an input guard, not a policy about how much a reviewer may write.
_MAX_NOTE_LENGTH: Final[int] = 4000

#: Most review statuses accepted in one request; there are four in the enumeration.
_MAX_STATUSES: Final[int] = 8

#: Longest address accepted, matching ``subscriber.email`` and RFC 5321's bound on a
#: reverse-path once the angle brackets are taken off.
_MAX_EMAIL_LENGTH: Final[int] = 254

#: Longest local part, per RFC 5321.
_MAX_EMAIL_LOCAL_LENGTH: Final[int] = 64

#: Longest subscription token accepted. The tokens this API issues are a fixed shape; the
#: bound stops a client making the server hash a megabyte.
_MAX_TOKEN_LENGTH: Final[int] = 128

# ASCII digits only: ``\d`` also matches other Unicode decimal digits, which ``int()``
# happily parses, and a parameter that reads as ``page=٤`` is not one this API accepts.
_INTEGER_RE: Final[re.Pattern[str]] = re.compile(r"^[+-]?[0-9]+$")
_DATE_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_CODE_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z0-9]{2,8}$")
_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
#: Keyword-list category, e.g. ``active_substance``. Underscored rather than hyphenated,
#: because these are the schema's own enum values and not slugs invented for a URL.
_CATEGORY_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z]+(?:_[a-z]+)*$")
_LANGUAGE_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})?$")
_SOURCE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:()\-/ ]*$")
_LIST_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

#: An email address, deliberately narrower than RFC 5322 allows. Quoted local parts, comments
#: and address literals are legal and are refused here: none of them appears in an address a
#: researcher subscribes with, and each is a way of smuggling something past a later reader.
#: Matched with ``fullmatch`` and against a value already checked for control characters, so
#: no line break can reach a mail header through it.
_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)

#: A subscription token: the selector and the verifier, separated by a dot.
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")

#: Control characters rejected in a stored free-text value. A reviewer identifier and a note
#: are read back out of the API, so they are kept free of anything that is not text.
_CONTROL_RE: Final[re.Pattern[str]] = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: Characters removed before a client value is reflected in an error message.
_UNSAFE_ECHO_RE: Final[re.Pattern[str]] = re.compile(r"[<>&\x00-\x1f\x7f]")

#: Any enumeration a query parameter can be parsed into.
_EnumT = TypeVar("_EnumT", bound=enum.StrEnum)


class ExportFormat(enum.StrEnum):
    """Serialisation offered by ``/api/cases/export``."""

    CSV = "csv"
    JSONL = "jsonl"


@dataclass(frozen=True, slots=True)
class CaseQuery:
    """A validated ``/api/cases`` request: what to select, and which page of it."""

    criteria: CaseSearchCriteria
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class ReviewQuery:
    """A validated ``/api/reviews`` request: which part of the queue, and which page."""

    criteria: ReviewSearchCriteria
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class ReviewDecisionInput:
    """A validated review verdict: the outcome, who reached it and why.

    ``decided_by`` is an opaque identifier and may name a person or an agent — the project
    has not decided which reviews the queue (core document section 2.7), so nothing here
    assumes either.
    """

    decision: ReviewDecision
    decided_by: str
    note: str | None


def _echo(value: str) -> str:
    """Return a value in a form that is safe to put in an error message.

    Naming the offending value is what makes a 400 actionable, but the value came from a
    client, so it is bounded in length and stripped of markup and control characters before
    it is reflected: the response is JSON, and a consumer that renders ``details.value``
    into a page should not be handed a fragment to render.

    Args:
        value: The raw client value.

    Returns:
        The sanitised value, truncated with an ellipsis if it exceeds the echo limit.
    """
    cleaned = _UNSAFE_ECHO_RE.sub("", value)
    if len(cleaned) <= _MAX_ECHO_LENGTH:
        return cleaned
    return f"{cleaned[:_MAX_ECHO_LENGTH]}..."


def _reject(parameter: str, message: str, **details: Any) -> ValidationError:  # noqa: ANN401 - detail values are arbitrary JSON scalars
    """Build the validation error for a bad parameter.

    Args:
        parameter: Name of the offending query parameter.
        message: Client-safe explanation.
        **details: Extra structured context, e.g. the accepted range.

    Returns:
        A :class:`~plt.api.errors.ValidationError` ready to be raised.
    """
    return ValidationError(message, details={"parameter": parameter, **details})


def _parse_int(
    args: MultiDict[str, str],
    parameter: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Parse a bounded integer query parameter.

    An out-of-range value is rejected rather than clamped, so a client is told that its
    request was not the one that was answered.

    Args:
        args: The request's query parameters.
        parameter: Parameter name.
        default: Value used when the parameter is absent or empty.
        minimum: Smallest accepted value.
        maximum: Largest accepted value.

    Returns:
        The parsed value, or ``default``.

    Raises:
        ValidationError: If the value is not an integer or falls outside the range.
    """
    raw = (args.get(parameter) or "").strip()
    if not raw:
        return default
    if not _INTEGER_RE.match(raw):
        raise _reject(parameter, f"{parameter} must be an integer.", value=_echo(raw))

    try:
        value = int(raw)
    except ValueError as exc:
        # CPython refuses to parse an integer literal of more than a few thousand digits.
        raise _reject(parameter, f"{parameter} must be an integer.", value=_echo(raw)) from exc

    if value < minimum or value > maximum:
        raise _reject(
            parameter,
            f"{parameter} must be between {minimum} and {maximum}.",
            value=_echo(raw),
            minimum=minimum,
            maximum=maximum,
        )
    return value


def _parse_text(
    args: MultiDict[str, str],
    parameter: str,
    *,
    max_length: int,
    pattern: re.Pattern[str] | None = None,
    transform: str | None = None,
) -> str | None:
    """Parse a bounded free-text query parameter.

    Args:
        args: The request's query parameters.
        parameter: Parameter name.
        max_length: Longest accepted value.
        pattern: Optional pattern the value must match in full.
        transform: ``"lower"`` or ``"upper"`` to normalise case before matching, or
            ``None`` to compare the value as the client sent it.

    Returns:
        The cleaned value, or ``None`` when the parameter is absent or empty.

    Raises:
        ValidationError: If the value is too long, contains a NUL byte or does not match.
    """
    raw = args.get(parameter)
    if raw is None:
        return None

    value = raw.strip()
    if not value:
        return None
    if "\x00" in value:
        raise _reject(parameter, f"{parameter} contains an illegal character.")
    if len(value) > max_length:
        raise _reject(
            parameter,
            f"{parameter} may not exceed {max_length} characters.",
            max_length=max_length,
        )

    if transform == "lower":
        value = value.lower()
    elif transform == "upper":
        value = value.upper()

    if pattern is not None and not pattern.match(value):
        raise _reject(parameter, f"{parameter} has an unexpected format.", value=_echo(value))
    return value


def _parse_date(args: MultiDict[str, str], parameter: str) -> date | None:
    """Parse an ISO ``YYYY-MM-DD`` date parameter.

    Args:
        args: The request's query parameters.
        parameter: Parameter name.

    Returns:
        The parsed date, or ``None`` when the parameter is absent or empty.

    Raises:
        ValidationError: If the value is not an ISO calendar date.
    """
    raw = (args.get(parameter) or "").strip()
    if not raw:
        return None
    if not _DATE_RE.match(raw):
        raise _reject(
            parameter,
            f"{parameter} must be an ISO date of the form YYYY-MM-DD.",
            value=_echo(raw),
        )
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise _reject(parameter, f"{parameter} is not a valid calendar date.") from exc


@overload
def _parse_enum(
    args: MultiDict[str, str], parameter: str, members: type[_EnumT], default: _EnumT
) -> _EnumT: ...


@overload
def _parse_enum(
    args: MultiDict[str, str], parameter: str, members: type[_EnumT], default: None = None
) -> _EnumT | None: ...


def _parse_enum(
    args: MultiDict[str, str],
    parameter: str,
    members: type[_EnumT],
    default: _EnumT | None = None,
) -> _EnumT | None:
    """Parse a query parameter whose accepted values are an enumeration.

    Args:
        args: The request's query parameters.
        parameter: Parameter name.
        members: The enumeration the value must belong to.
        default: Value used when the parameter is absent or empty.

    Returns:
        The matching member, or ``default``.

    Raises:
        ValidationError: If the value is not a member of the enumeration.
    """
    raw = (args.get(parameter) or "").strip().lower()
    if not raw:
        return default
    try:
        return members(raw)
    except ValueError as exc:
        raise _reject(
            parameter,
            f"{parameter} must be one of: {', '.join(sorted(member.value for member in members))}.",
            value=_echo(raw),
        ) from exc


def validate_jurisdiction_code(value: str, parameter: str = "jurisdiction") -> str:
    """Normalise and validate a single jurisdiction code.

    Args:
        value: The raw code, e.g. ``nl``.
        parameter: Parameter name used in an error envelope.

    Returns:
        The upper-cased code.

    Raises:
        ValidationError: If the code is not two to eight alphanumeric characters. An
            unknown but well-formed code is *not* an error: it simply matches no case.
    """
    code = value.strip().upper()
    if not _CODE_RE.match(code):
        raise _reject(
            parameter,
            f"{parameter} must be a two to eight character jurisdiction code.",
            value=_echo(value),
        )
    return code


def _parse_jurisdictions(args: MultiDict[str, str]) -> tuple[str, ...]:
    """Parse the repeatable ``jurisdiction`` parameter.

    Both ``?jurisdiction=NL&jurisdiction=EU`` and ``?jurisdiction=NL,EU`` are accepted;
    the first is what ``src/api/client.ts`` emits for an array value.

    Args:
        args: The request's query parameters.

    Returns:
        Distinct upper-cased codes, in the order the client listed them.

    Raises:
        ValidationError: If a code is malformed or too many were supplied.
    """
    codes: list[str] = []
    for raw in args.getlist("jurisdiction"):
        for part in raw.split(","):
            candidate = part.strip()
            if not candidate:
                continue
            code = validate_jurisdiction_code(candidate)
            if code not in codes:
                codes.append(code)

    if len(codes) > _MAX_JURISDICTIONS:
        raise _reject(
            "jurisdiction",
            f"At most {_MAX_JURISDICTIONS} jurisdictions may be requested at once.",
            maximum=_MAX_JURISDICTIONS,
        )
    return tuple(codes)


def parse_source_id(value: str) -> str:
    """Validate a source identifier taken from the URL path.

    Args:
        value: The raw path segment, e.g. ``ECLI:NL:HR:2020:1``.

    Returns:
        The stripped identifier.

    Raises:
        ValidationError: If the identifier is empty, over-long or contains characters no
            ECLI or CELEX number uses.
    """
    source_id = value.strip()
    too_long = len(source_id) > _MAX_SOURCE_ID_LENGTH
    if not source_id or too_long or not _SOURCE_ID_RE.match(source_id):
        raise _reject("source_id", "source_id has an unexpected format.", value=_echo(value))
    return source_id


def _parse_criteria(args: MultiDict[str, str]) -> CaseSearchCriteria:
    """Build search criteria from validated query parameters.

    ``include_unpublished`` is deliberately not readable from the request: the editorial
    switch is not a client concern, and the public API never exposes an unpublished case.

    Args:
        args: The request's query parameters.

    Returns:
        The populated :class:`~plt.db.repositories.CaseSearchCriteria`.

    Raises:
        ValidationError: If any parameter fails validation.
    """
    date_from = _parse_date(args, "date_from")
    date_to = _parse_date(args, "date_to")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise _reject("date_from", "date_from must not be later than date_to.")

    court = _parse_int(args, "court", default=0, minimum=1, maximum=2**31 - 1)

    return CaseSearchCriteria(
        query=_parse_text(args, "q", max_length=_MAX_QUERY_LENGTH),
        jurisdictions=_parse_jurisdictions(args),
        law_domain=_parse_enum(args, "law_domain", LawDomain),
        law_subfield=_parse_text(args, "law_subfield", max_length=_MAX_FILTER_LENGTH),
        topic_slug=_parse_text(
            args, "topic", max_length=_MAX_FILTER_LENGTH, pattern=_SLUG_RE, transform="lower"
        ),
        court_id=court or None,
        language=_parse_text(
            args, "language", max_length=_MAX_CODE_LENGTH, pattern=_LANGUAGE_RE, transform="lower"
        ),
        keyword=_parse_text(
            args, "keyword", max_length=_MAX_FILTER_LENGTH, pattern=_SLUG_RE, transform="lower"
        ),
        category=_parse_text(
            args, "category", max_length=_MAX_FILTER_LENGTH, pattern=_CATEGORY_RE, transform="lower"
        ),
        date_from=date_from,
        date_to=date_to,
        sort=_parse_enum(args, "sort", CaseSort, CaseSort.DATE_DESC),
    )


def parse_case_query(args: MultiDict[str, str], settings: Settings) -> CaseQuery:
    """Validate a ``/api/cases`` request.

    Args:
        args: The request's query parameters.
        settings: Settings supplying the pagination defaults and maxima.

    Returns:
        The validated :class:`CaseQuery`.

    Raises:
        ValidationError: If any parameter fails validation.
    """
    return CaseQuery(
        criteria=_parse_criteria(args),
        page=_parse_int(args, "page", default=1, minimum=1, maximum=2**31 - 1),
        page_size=_parse_int(
            args,
            "page_size",
            default=settings.page_size_default,
            minimum=1,
            maximum=settings.page_size_max,
        ),
    )


def parse_export_query(args: MultiDict[str, str]) -> CaseSearchCriteria:
    """Validate an ``/api/cases/export`` request's filters.

    Args:
        args: The request's query parameters.

    Returns:
        The validated criteria. The export is not paginated: it streams every match.

    Raises:
        ValidationError: If any parameter fails validation.
    """
    return _parse_criteria(args)


def parse_export_format(args: MultiDict[str, str]) -> ExportFormat:
    """Validate the ``format`` parameter of ``/api/cases/export``.

    Args:
        args: The request's query parameters.

    Returns:
        The requested format, defaulting to CSV.

    Raises:
        ValidationError: If the value is not a supported format.
    """
    return _parse_enum(args, "format", ExportFormat, ExportFormat.CSV)


def parse_latest_limit(args: MultiDict[str, str], settings: Settings) -> int:
    """Validate the ``limit`` parameter of ``/api/cases/latest``.

    Args:
        args: The request's query parameters.
        settings: Settings supplying the default and the maximum.

    Returns:
        The requested number of cases.

    Raises:
        ValidationError: If the value is not an integer within the configured bounds.
    """
    return _parse_int(
        args,
        "limit",
        default=settings.latest_limit_default,
        minimum=1,
        maximum=settings.latest_limit_max,
    )


def _parse_statuses(args: MultiDict[str, str]) -> tuple[ReviewStatus, ...]:
    """Parse the repeatable ``status`` parameter of the review queue.

    Args:
        args: The request's query parameters.

    Returns:
        The requested statuses, or the pending queue when none were named. ``status=any``
        selects every status, which is how a caller asks for the full history without having
        to know the members.

    Raises:
        ValidationError: If a value is not a review status or too many were supplied.
    """
    raw_values = [
        part.strip().lower()
        for raw in args.getlist("status")
        for part in raw.split(",")
        if part.strip()
    ]
    if not raw_values:
        return (ReviewStatus.PENDING,)
    if "any" in raw_values:
        return ()
    if len(raw_values) > _MAX_STATUSES:
        raise _reject(
            "status",
            f"At most {_MAX_STATUSES} statuses may be requested at once.",
            maximum=_MAX_STATUSES,
        )

    statuses: list[ReviewStatus] = []
    for value in raw_values:
        try:
            status = ReviewStatus(value)
        except ValueError as exc:
            members = ", ".join(sorted(member.value for member in ReviewStatus))
            raise _reject(
                "status",
                f"status must be one of: {members}, or 'any'.",
                value=_echo(value),
            ) from exc
        if status not in statuses:
            statuses.append(status)
    return tuple(statuses)


def parse_review_query(args: MultiDict[str, str], settings: Settings) -> ReviewQuery:
    """Validate an ``/api/reviews`` request.

    Args:
        args: The request's query parameters.
        settings: Settings supplying the pagination default and maximum.

    Returns:
        The validated :class:`ReviewQuery`.

    Raises:
        ValidationError: If any parameter fails validation.
    """
    criteria = ReviewSearchCriteria(
        statuses=_parse_statuses(args),
        jurisdictions=_parse_jurisdictions(args),
        list_version=_parse_text(
            args, "list_version", max_length=_MAX_CODE_LENGTH * 4, pattern=_LIST_VERSION_RE
        ),
        decided_by=_parse_text(args, "decided_by", max_length=_MAX_DECIDED_BY_LENGTH),
        sort=_parse_enum(args, "sort", ReviewSort, ReviewSort.FLAGGED_ASC),
    )
    return ReviewQuery(
        criteria=criteria,
        page=_parse_int(args, "page", default=1, minimum=1, maximum=2**31 - 1),
        page_size=_parse_int(
            args,
            "page_size",
            default=settings.review_page_size_default,
            minimum=1,
            maximum=settings.page_size_max,
        ),
    )


def _required_text(payload: dict[str, Any], field: str, *, max_length: int) -> str:
    """Read a required free-text member of a JSON request body.

    Args:
        payload: The parsed request body.
        field: Member name.
        max_length: Longest accepted value.

    Returns:
        The stripped value.

    Raises:
        ValidationError: If the member is missing, not a string, empty, over-long or carries
            a control character.
    """
    raw = payload.get(field)
    if not isinstance(raw, str):
        raise _reject(field, f"{field} is required and must be a string.")
    value = raw.strip()
    if not value:
        raise _reject(field, f"{field} must not be empty.")
    if len(value) > max_length:
        raise _reject(
            field,
            f"{field} may not exceed {max_length} characters.",
            max_length=max_length,
        )
    if _CONTROL_RE.search(value):
        raise _reject(field, f"{field} contains an illegal character.")
    return value


def parse_review_decision(body: object) -> ReviewDecisionInput:
    """Validate the body of a review decision.

    Args:
        body: The parsed JSON request body.

    Returns:
        The validated :class:`ReviewDecisionInput`.

    Raises:
        ValidationError: If the body is not an object, the outcome is not one of the two
            recorded verdicts, or the reviewer identifier is missing or malformed.
    """
    if not isinstance(body, dict):
        raise _reject("body", "The request body must be a JSON object.")
    payload: dict[str, Any] = body

    raw_decision = payload.get("decision")
    if not isinstance(raw_decision, str):
        raise _reject("decision", "decision is required and must be a string.")
    try:
        decision = ReviewDecision(raw_decision.strip().lower())
    except ValueError as exc:
        members = ", ".join(sorted(member.value for member in ReviewDecision))
        raise _reject(
            "decision",
            f"decision must be one of: {members}.",
            value=_echo(raw_decision),
        ) from exc

    note = payload.get("note")
    if note is not None and not isinstance(note, str):
        raise _reject("note", "note must be a string.")
    if isinstance(note, str):
        note = note.strip()
        if len(note) > _MAX_NOTE_LENGTH:
            raise _reject(
                "note",
                f"note may not exceed {_MAX_NOTE_LENGTH} characters.",
                max_length=_MAX_NOTE_LENGTH,
            )
        if _CONTROL_RE.search(note):
            raise _reject("note", "note contains an illegal character.")

    return ReviewDecisionInput(
        decision=decision,
        decided_by=_required_text(payload, "decided_by", max_length=_MAX_DECIDED_BY_LENGTH),
        note=note or None,
    )


def parse_subscription_request(body: object) -> str:
    """Validate and normalise the address a signup form submitted.

    Validation here is not about politeness to the user: this is the input to an
    unauthenticated endpoint that sends mail to whatever it is given, so an address is
    checked, bounded and normalised before anything downstream acts on it. The failures it
    reports are all decidable from the request alone — a syntax error is not a statement about
    who is on the list, so rejecting one is not an enumeration oracle.

    Args:
        body: The parsed JSON request body.

    Returns:
        The address as :func:`plt.notifications.pseudonyms.normalise_address` gives it: lower
        cased throughout, not only in the domain, so a reader who signs up twice with
        different capitals is one subscriber rather than two copies of one person's data
        receiving two digests a week.

        **Normalising here and hashing there must not be two rules.** The address stored by
        this function is the address the keyed digest is computed over when the subscription
        ends (core document section 2.12), and if the two ever disagreed a returning address
        would stop being recognised without anything failing — it would simply look like a
        first-time subscriber. Hence the shared function rather than a second ``.lower()``.

    Raises:
        ValidationError: If the body is not an object, or the address is missing, over-long,
            malformed or carries a control character.
    """
    if not isinstance(body, dict):
        raise _reject("body", "The request body must be a JSON object.")
    payload: dict[str, Any] = body

    raw = payload.get("email")
    if not isinstance(raw, str):
        raise _reject("email", "email is required and must be a string.")
    value = raw.strip()
    if not value:
        raise _reject("email", "email must not be empty.")
    if len(value) > _MAX_EMAIL_LENGTH:
        raise _reject(
            "email",
            f"email may not exceed {_MAX_EMAIL_LENGTH} characters.",
            max_length=_MAX_EMAIL_LENGTH,
        )
    if _CONTROL_RE.search(value) or "\x00" in value:
        raise _reject("email", "email contains an illegal character.")
    if not _EMAIL_RE.fullmatch(value):
        raise _reject("email", "email is not a valid address.", value=_echo(value))
    local, _, _domain = value.partition("@")
    if len(local) > _MAX_EMAIL_LOCAL_LENGTH:
        raise _reject("email", "email is not a valid address.", value=_echo(value))
    return normalise_address(value)


def parse_subscription_token(body: object) -> str:
    """Validate the token a confirmation or unsubscribe link carried.

    Only the *shape* is checked here. Whether the token verifies is decided by
    :func:`plt.notifications.tokens.verify_token`, in constant time, and a token that does not
    verify is reported exactly as one that was never issued.

    Args:
        body: The parsed JSON request body.

    Returns:
        The token.

    Raises:
        ValidationError: If the body is not an object, or the token is missing, over-long or
            not the shape a token has.
    """
    if not isinstance(body, dict):
        raise _reject("body", "The request body must be a JSON object.")
    payload: dict[str, Any] = body

    raw = payload.get("token")
    if not isinstance(raw, str):
        raise _reject("token", "token is required and must be a string.")
    value = raw.strip()
    if not value:
        raise _reject("token", "token must not be empty.")
    if len(value) > _MAX_TOKEN_LENGTH or not _TOKEN_RE.fullmatch(value):
        # The value is never echoed: it is a credential, and an error message is one of the
        # places a credential ends up in a log.
        raise _reject("token", "token is not a valid subscription token.")
    return value


def _iso(value: date | datetime | None) -> str | None:
    """Render a date or timestamp as an ISO 8601 string.

    Args:
        value: The value to render.

    Returns:
        The ISO representation, or ``None`` when the value is unset.
    """
    return None if value is None else value.isoformat()


def _enum_value(value: enum.StrEnum | None) -> str | None:
    """Render an enumeration member as the lower-case string the schema persists.

    Args:
        value: The member to render.

    Returns:
        The member's value, or ``None`` when the column is unset.
    """
    return None if value is None else str(value.value)


def case_summary_payload(case: Case) -> dict[str, Any]:
    """Serialise a case for a list response.

    The jurisdiction and court names are flattened onto the record rather than nested in
    an object, so a result card needs neither a second request nor a lookup table, and the
    JSON, the JSON-Lines export and the CSV export all name a field the same way.
    ``court_id`` is carried alongside the name because the ``court`` filter takes an id, so
    a card can link to "other cases from this court" without resolving the name first.

    Both names come from many-to-one relationships resolved through the session's identity
    map, so a page of results costs one extra query per distinct jurisdiction and court,
    not one per row.

    Args:
        case: The case to serialise.

    Returns:
        The wire representation of a search result.
    """
    return {
        "id": case.id,
        "jurisdiction_code": case.jurisdiction_code,
        "jurisdiction_name": case.jurisdiction.name,
        "source_id": case.source_id,
        "source_system": case.source_system,
        "court_id": case.court_id,
        "court_name": None if case.court is None else case.court.name,
        "title": case.title,
        "abstract": case.abstract,
        "decision_date": _iso(case.decision_date),
        "publication_date": _iso(case.publication_date),
        "case_numbers": list(case.case_numbers),
        "language": case.language,
        "law_domain": _enum_value(case.law_domain),
        "law_subfield": case.law_subfield,
        "procedure_type": case.procedure_type,
        "outcome": case.outcome,
        "source_url": case.source_url,
    }


def _document_payload(document: CaseDocument) -> dict[str, Any]:
    """Serialise one document of a case.

    ``raw_payload`` is never exposed: it is the verbatim source response, kept for
    reclassification, and can be megabytes of markup.

    Args:
        document: The document to serialise.

    Returns:
        The wire representation of the document, full text included.
    """
    return {
        "id": document.id,
        "language": document.language,
        "doc_type": _enum_value(document.doc_type),
        "format": document.format,
        "full_text": document.full_text,
        "source_url": document.source_url,
        "byte_size": document.byte_size,
        "retrieved_at": _iso(document.retrieved_at),
    }


def _party_payload(party: Party) -> dict[str, Any]:
    """Serialise one litigating party.

    Args:
        party: The party to serialise.

    Returns:
        The wire representation of the party.
    """
    return {
        "id": party.id,
        "name": party.name,
        "role": _enum_value(party.role),
        "party_type": party.party_type,
        "ordinal": party.ordinal,
    }


def _topic_payload(assignment: CaseTopic) -> dict[str, Any]:
    """Serialise one topic assignment.

    Args:
        assignment: The case-topic association to serialise.

    Returns:
        The wire representation of the assignment, including its provenance.
    """
    return {
        "slug": assignment.topic.slug,
        "label": assignment.topic.label,
        "confidence": assignment.confidence,
        "assigned_by": _enum_value(assignment.assigned_by),
    }


def _match_payload(match: KeywordMatch) -> dict[str, Any]:
    """Serialise one keyword-list match.

    Args:
        match: The match to serialise.

    Returns:
        The wire representation of the match, which is what makes the classification of a
        case auditable by a reader.
    """
    return {
        "term_id": match.term_id,
        "term": match.term,
        "category": match.category,
        "list_version": match.list_version,
        "field": match.field,
        "match_count": match.match_count,
        "snippet": match.snippet,
    }


def _citation_payload(citation: Citation) -> dict[str, Any]:
    """Serialise one citation.

    Args:
        citation: The citation to serialise.

    Returns:
        The wire representation of the citation.
    """
    return {
        "target_identifier": citation.target_identifier,
        "target_scheme": citation.target_scheme,
        "citation_type": citation.citation_type,
        "target_title": citation.target_title,
        "target_url": citation.target_url,
    }


def case_detail_payload(case: Case) -> dict[str, Any]:
    """Serialise a case with its documents, parties, topics and matched terms.

    Args:
        case: The case to serialise, loaded with its detail relationships.

    Returns:
        The wire representation of ``/api/cases/<jurisdiction>/<source_id>``.
    """
    payload = case_summary_payload(case)
    payload.update(
        {
            "filing_date": _iso(case.filing_date),
            "revision": case.revision,
            "first_seen_at": _iso(case.first_seen_at),
            "last_seen_at": _iso(case.last_seen_at),
            "updated_at": _iso(case.updated_at),
            "documents": [_document_payload(document) for document in case.documents],
            "parties": [_party_payload(party) for party in case.parties],
            "topics": [_topic_payload(assignment) for assignment in case.topics],
            "keyword_matches": [_match_payload(match) for match in case.keyword_matches],
            "citations": [_citation_payload(citation) for citation in case.citations],
        }
    )
    return payload


def page_payload(page: CasePage) -> dict[str, Any]:
    """Serialise a page of search results.

    Args:
        page: The page returned by :func:`~plt.db.repositories.search_cases`.

    Returns:
        The paginated envelope the frontend's ``Paginated<T>`` describes, plus the derived
        ``page_count`` and ``has_next`` so a paginator needs no arithmetic of its own.
    """
    return {
        "items": [case_summary_payload(case) for case in page.items],
        "page": page.page,
        "page_size": page.page_size,
        "total": page.total,
        "page_count": page.page_count,
        "has_next": page.has_next,
    }


def _review_decision_payload(entry: CaseReviewDecision) -> dict[str, Any]:
    """Serialise one entry of a review item's decision history.

    Args:
        entry: The recorded decision.

    Returns:
        The wire representation, including the revision it was taken on — which is what
        makes a superseded verdict readable rather than merely old.
    """
    return {
        "decision": _enum_value(entry.decision),
        "decided_by": entry.decided_by,
        "decided_at": _iso(entry.decided_at),
        "note": entry.note,
        "case_revision": entry.case_revision,
        "content_hash": entry.content_hash,
    }


def review_item_payload(review: CaseReview) -> dict[str, Any]:
    """Serialise one item of the review queue.

    Everything a reviewer needs to decide travels with the item: the case itself, the score
    against the band it was judged in, the list version that produced the flag, and the
    matched terms with their fields, weights and snippets. None of it is recomputed — it is
    what the run recorded, which is what makes an individual verdict explainable after the
    list has moved on (``docs/core-document.md`` section 2.8).

    Args:
        review: The review item, loaded with its case, that case's keyword matches and its
            decision history.

    Returns:
        The wire representation of a queue entry.
    """
    case = review.case
    return {
        "id": review.id,
        "status": _enum_value(review.status),
        "matched_term_count": review.case.matched_term_count,
        "list_version": review.list_version,
        "reason": review.reason,
        "flagged_at": _iso(review.flagged_at),
        "flagged_revision": review.flagged_revision,
        "flagged_content_hash": review.flagged_content_hash,
        "decision": _enum_value(review.decision),
        "decided_by": review.decided_by,
        "decided_at": _iso(review.decided_at),
        "decided_revision": review.decided_revision,
        "decision_note": review.decision_note,
        # A decision taken before the current revision no longer covers the text in front of
        # the reviewer. Stated here rather than left to the client to derive from two
        # revision numbers, because getting it wrong means inheriting a stale verdict.
        "decision_is_current": review.decision is not None
        and review.decided_revision == case.revision,
        "case_revision": case.revision,
        "published": case.is_published,
        "case": case_summary_payload(case),
        "keyword_matches": [_match_payload(match) for match in case.keyword_matches],
        "decisions": [_review_decision_payload(entry) for entry in review.decisions],
    }


def review_page_payload(page: ReviewPage) -> dict[str, Any]:
    """Serialise a page of the review queue.

    Args:
        page: The page returned by :func:`~plt.db.repositories.search_reviews`.

    Returns:
        The same paginated envelope every list endpoint uses (architecture section 5.1).
    """
    return {
        "items": [review_item_payload(review) for review in page.items],
        "page": page.page,
        "page_size": page.page_size,
        "total": page.total,
        "page_count": page.page_count,
        "has_next": page.has_next,
    }


def jurisdiction_stat_payload(stat: JurisdictionStat) -> dict[str, Any]:
    """Serialise one entry of the map payload.

    Args:
        stat: The aggregate row for one jurisdiction.

    Returns:
        The wire representation, including jurisdictions whose ``case_count`` is zero so
        the map can render intended coverage rather than only what has been ingested.
    """
    return {
        "code": stat.code,
        "name": stat.name,
        "type": _enum_value(stat.type),
        "map_feature_id": stat.map_feature_id,
        "is_active": stat.is_active,
        "case_count": stat.case_count,
        "latest_decision_date": _iso(stat.latest_decision_date),
    }


def _keyword_options(facets: FacetValues, settings: Settings) -> list[dict[str, Any]]:
    """Build the keyword dropdown's options from the curated lists themselves.

    The roster is the list, not the database. A term that is in the list and has selected
    nothing is a fact worth seeing — it is how a curator notices that a substance never
    appears in the case law, or that a term is misspelled — so it appears with a count of
    zero rather than being silently absent.

    A list that cannot be loaded is skipped rather than fatal: the filter UI is not the place
    a broken keyword file should surface, and the ingestion run says so far more loudly.

    Args:
        facets: The counts of published cases per term id.
        settings: Settings resolving the keyword directory.

    Returns:
        Every curated term, sorted by label, each with its id, category, jurisdiction and
        the number of published cases carrying it.
    """
    options: list[dict[str, Any]] = []
    for code, _ in facets.jurisdictions:
        try:
            keyword_list = load_keyword_list_for(code, settings)
        except KeywordListError:
            log.warning(
                "no usable keyword list for %s; its terms are left out of /api/filters", code
            )
            continue
        options.extend(
            {
                "id": term.term_id,
                # The label, never the raw term: a regex term's term is a pattern, and this
                # roster is what the keyword dropdown shows a reader.
                "term": term.public_label,
                "category": term.category,
                "jurisdiction": keyword_list.jurisdiction,
                "case_count": facets.keyword_counts.get(term.term_id, 0),
            }
            for term in keyword_list.terms.values()
        )
    options.sort(key=lambda option: (str(option["term"]).lower(), str(option["jurisdiction"])))
    return options


def exclusions_payload(facets: FacetValues, settings: Settings) -> dict[str, Any]:
    """Serialise what each jurisdiction's criterion deliberately keeps out.

    An inclusion criterion is only half a method. A systematic review is expected to publish
    its exclusion criteria too, and a keyword list normally cannot show them: the terms that
    were considered and rejected leave no trace in the list itself. They leave one here,
    because the curation record is kept as data.

    Three mechanisms, which are genuinely different and are not merged:

    * ``excluded_terms`` -- considered, rejected, and recorded with the reason.
    * ``gated_terms`` -- on the list, but unable to include a case unless another term does
      first. The gate is named, so a reader can see what has to be present.
    * ``exclusion_patterns`` -- phrases that veto a document outright, however many terms it
      matched.

    A jurisdiction whose keyword list cannot be read is skipped rather than failing the
    request: this feeds a page, and one broken list should not take the others down with it.

    Args:
        facets: Supplies the jurisdictions that have published cases, and their names.
        settings: Settings resolving the keywords directory.

    Returns:
        The wire representation of ``/api/exclusions``.
    """
    jurisdictions: list[dict[str, Any]] = []
    for code, name in facets.jurisdictions:
        try:
            keyword_list = load_keyword_list_for(code, settings)
        except KeywordListError:
            log.warning("no usable keyword list for %s; it is left out of /api/exclusions", code)
            continue
        jurisdictions.append(
            {
                "code": keyword_list.jurisdiction,
                "name": name,
                "list_version": keyword_list.list_version,
                "excluded_terms": [
                    {
                        "id": excluded.term_id,
                        "term": excluded.term,
                        "category": excluded.category,
                        "reason": excluded.reason,
                    }
                    for excluded in load_excluded_terms_for(code, settings)
                ],
                "gated_terms": [
                    {
                        "id": term.term_id,
                        "term": term.public_label,
                        "category": term.category,
                        # The gate is named rather than merely flagged: "cannot stand alone"
                        # invites the question "alone against what", and this answers it.
                        "requires": [
                            keyword_list.terms[gate].public_label
                            for gate in term.requires
                            if gate in keyword_list.terms
                        ],
                    }
                    for term in keyword_list.terms.values()
                    if term.requires
                ],
                "exclusion_patterns": [
                    {"pattern": exclusion.pattern, "reason": exclusion.reason}
                    for exclusion in keyword_list.exclusions
                ],
            }
        )
    return {"jurisdictions": jurisdictions}


def facets_payload(facets: FacetValues, settings: Settings) -> dict[str, Any]:
    """Serialise the facet values behind the All-cases filter UI.

    The bounds are included so the filter UI can constrain its own controls from the
    server's configuration instead of repeating the numbers in the frontend.

    Args:
        facets: The distinct values found on published cases.
        settings: Settings supplying the pagination bounds and the keyword directory.

    Returns:
        The wire representation of ``/api/filters``.
    """
    return {
        "jurisdictions": [{"code": code, "name": name} for code, name in facets.jurisdictions],
        "courts": [{"id": court_id, "name": name} for court_id, name in facets.courts],
        "law_domains": list(facets.law_domains),
        "law_subfields": list(facets.law_subfields),
        "languages": list(facets.languages),
        "topics": [{"slug": slug, "label": label} for slug, label in facets.topics],
        "keywords": _keyword_options(facets, settings),
        "categories": list(facets.categories),
        "decision_date_range": {
            "from": _iso(facets.earliest_decision_date),
            "to": _iso(facets.latest_decision_date),
        },
        "sorts": [str(member.value) for member in CaseSort],
        "export_formats": [str(member.value) for member in ExportFormat],
        "page_size_default": settings.page_size_default,
        "page_size_max": settings.page_size_max,
        "latest_limit_max": settings.latest_limit_max,
    }


def health_ingest_payload(runs: Iterable[IngestRunSummary]) -> dict[str, str | None]:
    """Serialise the last successful ingest per jurisdiction for ``/api/health``.

    Args:
        runs: One summary per jurisdiction that has completed a run.

    Returns:
        A mapping of jurisdiction code onto the ISO timestamp the run finished at, which is
        the shape ``HealthResponse.ingest`` declares in the frontend's ``types/api.ts``.
    """
    return {run.jurisdiction_code: _iso(run.finished_at) for run in runs}


#: Column order of the CSV export. The JSON-Lines export emits the same keys per line, so
#: the two formats carry identical information.
EXPORT_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "jurisdiction_code",
    "jurisdiction_name",
    "source_id",
    "source_system",
    "court_name",
    "title",
    "abstract",
    "decision_date",
    "filing_date",
    "publication_date",
    "case_numbers",
    "language",
    "law_domain",
    "law_subfield",
    "procedure_type",
    "outcome",
    "source_url",
    "revision",
    "first_seen_at",
    "last_seen_at",
)


def export_row(
    case: Case,
    jurisdiction_name: str | None = None,
    court_name: str | None = None,
) -> dict[str, Any]:
    """Flatten a case into one export record.

    The two names are passed in rather than read from ``case.jurisdiction`` and
    ``case.court``: an export iterates tens of thousands of rows without holding on to
    them, so a relationship access would risk a lazy load per row once the session's weak
    identity map has dropped the parent.

    Args:
        case: The case to export.
        jurisdiction_name: Name of the case's jurisdiction, resolved by the caller.
        court_name: Name of the case's court, resolved by the caller.

    Returns:
        A mapping keyed by :data:`EXPORT_COLUMNS`. Values are scalars so the same record
        serves the CSV writer and the JSON-Lines writer.
    """
    return {
        "id": case.id,
        "jurisdiction_code": case.jurisdiction_code,
        "jurisdiction_name": jurisdiction_name,
        "source_id": case.source_id,
        "source_system": case.source_system,
        "court_name": court_name,
        "title": case.title,
        "abstract": case.abstract,
        "decision_date": _iso(case.decision_date),
        "filing_date": _iso(case.filing_date),
        "publication_date": _iso(case.publication_date),
        "case_numbers": " ".join(case.case_numbers),
        "language": case.language,
        "law_domain": _enum_value(case.law_domain),
        "law_subfield": case.law_subfield,
        "procedure_type": case.procedure_type,
        "outcome": case.outcome,
        "source_url": case.source_url,
        "revision": case.revision,
        "first_seen_at": _iso(case.first_seen_at),
        "last_seen_at": _iso(case.last_seen_at),
    }


def export_metadata(criteria: CaseSearchCriteria, generated_at: datetime) -> dict[str, Any]:
    """Describe the export itself, so a downloaded file records how it was produced.

    Args:
        criteria: The filters the export was taken with.
        generated_at: When the export started.

    Returns:
        A mapping emitted as the first JSON-Lines record and as ``#``-prefixed comment
        lines in the CSV.
    """
    return {
        "record_type": "metadata",
        "generated_at": _iso(generated_at),
        "source": "Pesticide Litigation Tracker",
        "filters": {
            "q": criteria.query,
            "jurisdiction": list(criteria.jurisdictions),
            "law_domain": _enum_value(criteria.law_domain),
            "law_subfield": criteria.law_subfield,
            "topic": criteria.topic_slug,
            "court": criteria.court_id,
            "language": criteria.language,
            "date_from": _iso(criteria.date_from),
            "date_to": _iso(criteria.date_to),
            "sort": str(criteria.sort.value),
        },
    }


def csv_cell(value: Any) -> str:  # noqa: ANN401 - accepts any scalar an export row holds
    """Render one export value as CSV text, neutralising spreadsheet formulas.

    A cell beginning with ``=``, ``+``, ``-``, ``@`` or a control character is executed as
    a formula by Excel and LibreOffice when the file is opened. Case titles come from
    public sources and are not trusted to stay free of them, so such a cell is prefixed
    with an apostrophe: the value still reads correctly and no longer evaluates.

    Args:
        value: The value to render.

    Returns:
        The cell text.
    """
    if value is None:
        return ""
    text = str(value)
    if text[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return f"'{text}"
    return text


def csv_record(row: dict[str, Any]) -> Sequence[str]:
    """Render an export row as the CSV cells of :data:`EXPORT_COLUMNS`.

    Args:
        row: The row produced by :func:`export_row`.

    Returns:
        One cell per column, in column order.
    """
    return [csv_cell(row[column]) for column in EXPORT_COLUMNS]
