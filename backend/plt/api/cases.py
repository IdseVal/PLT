"""Case endpoints: ``/api/cases`` and below.

The four routes fixed by ``docs/architecture.md`` section 5: search, the sidebar feed, one
case by its source identifier, and the streaming export.

No SQL is written here. A request becomes a validated
:class:`~plt.db.repositories.CaseSearchCriteria` in :mod:`plt.api.schemas` and is executed
by the repository helpers, which bind every value as a parameter. That seam is deliberate:
``relevance`` currently ranks a title hit above an abstract hit above a full-text hit
because the schema stays portable between SQLite and PostgreSQL, and replacing it with a
PostgreSQL full-text index is a change to the repository layer alone.

The export streams. It opens its own session, iterates the result set in batches and yields
each record as it is rendered, so exporting the whole corpus costs a bounded amount of
memory (architecture rule 2.3) and a client that disconnects halfway closes the session
through the generator's ``finally``.
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from flask import Blueprint, Response, request

from plt.api.errors import NotFoundError
from plt.api.schemas import (
    EXPORT_COLUMNS,
    ExportFormat,
    case_detail_payload,
    case_summary_payload,
    csv_record,
    export_metadata,
    export_row,
    page_payload,
    parse_case_query,
    parse_export_format,
    parse_export_query,
    parse_latest_limit,
    parse_source_id,
    validate_jurisdiction_code,
)
from plt.db.repositories import (
    CaseSearchCriteria,
    get_case_by_source_id,
    latest_cases,
    list_facets,
    search_cases,
    stream_cases,
)
from plt.extensions import current_settings, db_session, limiter, session_factory
from plt.utils.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session, sessionmaker

__all__ = ["cases_bp"]

log = get_logger(__name__)

#: Mounted at ``<api_prefix>/cases`` by :func:`plt.api.register_blueprints`.
cases_bp = Blueprint("cases", __name__)

#: Line terminator for the CSV export. RFC 4180 asks for CRLF.
_CSV_LINE_TERMINATOR = "\r\n"


def _export_rate_limit() -> str:
    """Return the configured rate limit for the export endpoint.

    Evaluated per request rather than at import time, so the limit stays configuration and
    a test application can carry a different one.

    Returns:
        A limit expression such as ``10 per hour``.
    """
    return current_settings().rate_limit_export


@cases_bp.get("")
def list_cases() -> tuple[dict[str, Any], int]:
    """Search, filter and paginate the case corpus.

    Query parameters are those of architecture section 5: ``q``, ``jurisdiction``
    (repeatable), ``law_domain``, ``law_subfield``, ``topic``, ``court``, ``language``,
    ``date_from``, ``date_to``, ``sort``, ``page`` and ``page_size``.

    Returns:
        A ``(payload, status)`` pair carrying one page of results and its totals.
    """
    settings = current_settings()
    query = parse_case_query(request.args, settings)
    page = search_cases(
        db_session(),
        query.criteria,
        page=query.page,
        page_size=query.page_size,
    )
    return page_payload(page), HTTPStatus.OK


@cases_bp.get("/latest")
def latest() -> tuple[dict[str, Any], int]:
    """Return the most recently decided cases for the home-page sidebar.

    Returns:
        A ``(payload, status)`` pair carrying up to ``limit`` cases, newest first.
    """
    settings = current_settings()
    limit = parse_latest_limit(request.args, settings)
    cases = latest_cases(db_session(), limit=limit)
    payload = {"items": [case_summary_payload(case) for case in cases], "limit": limit}
    return payload, HTTPStatus.OK


@cases_bp.get("/export")
@limiter.limit(_export_rate_limit)
def export() -> Response:
    """Stream every case matching the filters as CSV or JSON-Lines.

    Takes the same filters as ``/api/cases`` plus ``format`` (``csv`` by default, or
    ``jsonl``). The response is not paginated and is generated incrementally.

    Returns:
        A streaming :class:`flask.Response` with a download filename.
    """
    criteria = parse_export_query(request.args)
    export_format = parse_export_format(request.args)
    generated_at = datetime.now(tz=UTC)

    # The session factory is resolved while the application context is still current: the
    # generator below runs after this view has returned.
    factory = session_factory()
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    filename = f"plt-cases-{stamp}.{export_format.value}"

    if export_format is ExportFormat.JSONL:
        body = _stream_jsonl(factory, criteria, generated_at)
        mimetype = "application/x-ndjson"
    else:
        body = _stream_csv(factory, criteria)
        mimetype = "text/csv"

    response = Response(body, mimetype=mimetype)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["X-PLT-Generated-At"] = generated_at.isoformat()
    # An export is a snapshot of a moving corpus; never let an intermediary cache it.
    response.headers["Cache-Control"] = "no-store"
    return response


def _name_lookups(session: Session) -> tuple[dict[str, str], dict[int, str]]:
    """Return the jurisdiction and court names an export needs, in a few small queries.

    Resolving the names up front rather than through each row's relationships keeps the
    export at a fixed number of queries: the alternative is a lazy load per row whenever
    the session's weak identity map has dropped the parent object.

    Args:
        session: Open database session.

    Returns:
        A ``(jurisdiction name by code, court name by id)`` pair. Only jurisdictions and
        courts carrying a published case appear, which is exactly the set an export can
        reach.
    """
    facets = list_facets(session)
    return dict(facets.jurisdictions), dict(facets.courts)


def _stream_csv(factory: sessionmaker[Session], criteria: CaseSearchCriteria) -> Iterator[str]:
    """Yield the export as CSV, one row at a time.

    Args:
        factory: Session factory to open the export's own session with.
        criteria: The validated filters.

    Yields:
        The header line, then one line per matching case.
    """
    writer = csv.writer(_LineBuffer(), lineterminator=_CSV_LINE_TERMINATOR)
    session = factory()
    try:
        yield str(writer.writerow(EXPORT_COLUMNS))
        jurisdictions, courts = _name_lookups(session)
        for case in stream_cases(session, criteria):
            row = export_row(
                case,
                jurisdictions.get(case.jurisdiction_code),
                None if case.court_id is None else courts.get(case.court_id),
            )
            yield str(writer.writerow(csv_record(row)))
    except Exception:
        # The status line is long gone, so the failure cannot be reported in-band. Log it
        # and let the aborted transfer tell the client the file is incomplete.
        log.exception("case export failed mid-stream", extra={"format": "csv"})
        raise
    finally:
        session.close()


def _stream_jsonl(
    factory: sessionmaker[Session],
    criteria: CaseSearchCriteria,
    generated_at: datetime,
) -> Iterator[str]:
    """Yield the export as JSON-Lines, one record at a time.

    The first line describes the export itself - when it was taken and with which filters -
    so a downloaded file records how it was produced; every later line is one case tagged
    ``record_type: case``.

    Args:
        factory: Session factory to open the export's own session with.
        criteria: The validated filters.
        generated_at: When the export started.

    Yields:
        One JSON document per line.
    """
    session = factory()
    try:
        yield _json_line(export_metadata(criteria, generated_at))
        jurisdictions, courts = _name_lookups(session)
        for case in stream_cases(session, criteria):
            row = export_row(
                case,
                jurisdictions.get(case.jurisdiction_code),
                None if case.court_id is None else courts.get(case.court_id),
            )
            yield _json_line({"record_type": "case", **row})
    except Exception:
        log.exception("case export failed mid-stream", extra={"format": "jsonl"})
        raise
    finally:
        session.close()


def _json_line(payload: dict[str, Any]) -> str:
    """Render one JSON-Lines record.

    Args:
        payload: The record to render.

    Returns:
        A single-line JSON document terminated by a newline.
    """
    return json.dumps(payload, ensure_ascii=False, default=str) + "\n"


class _LineBuffer:
    """A file-like object for :mod:`csv` that returns each rendered line instead of storing it.

    ``csv.writer.writerow`` returns whatever the underlying ``write`` returns, which is how
    a row is rendered without accumulating the document.
    """

    def write(self, line: str) -> str:
        """Return the line the CSV writer produced.

        Args:
            line: The rendered CSV line.

        Returns:
            The same line.
        """
        return line


@cases_bp.get("/<jurisdiction>/<source_id>")
def detail(jurisdiction: str, source_id: str) -> tuple[dict[str, Any], int]:
    """Return one case with its documents, parties, topics and matched terms.

    Args:
        jurisdiction: Jurisdiction code from the path, e.g. ``NL``.
        source_id: ECLI or CELEX identifier from the path.

    Returns:
        A ``(payload, status)`` pair carrying the full case.

    Raises:
        NotFoundError: If no published case has that identifier in that jurisdiction.
    """
    code = validate_jurisdiction_code(jurisdiction)
    identifier = parse_source_id(source_id)

    case = get_case_by_source_id(db_session(), code, identifier, with_details=True)
    if case is None or not case.is_published:
        # An unpublished case is reported as missing rather than as forbidden: its
        # existence is an editorial matter and not the public API's to disclose.
        message = "No case exists with that identifier in that jurisdiction."
        raise NotFoundError(
            message,
            details={"jurisdiction": code, "source_id": identifier},
        )
    return case_detail_payload(case), HTTPStatus.OK
