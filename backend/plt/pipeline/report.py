"""The match report a run writes about the documents it judged.

``--dry-run`` runs the whole chain — discover, deduplicate, fetch, normalise, filter — and
writes this instead of touching the database (``docs/architecture.md`` section 4). That makes
it the tool for tuning a keyword list: a curator can widen a list, re-run the same window
against a real source, and read exactly which cases the change let in and on which terms,
without a single row being written.

The format is JSON Lines: one self-contained JSON object per document, appended and flushed
as the run proceeds. Two reasons, both practical. A run over a large window never holds the
report in memory, and a run that is interrupted halfway still leaves a readable file, because
every line before the last is complete.

Rejected documents are reported as well as accepted ones. A recall problem — the case that
should have matched and did not — is invisible in a report that only lists successes.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self, TextIO

from plt.db.base import utcnow
from plt.pipeline.base import NormalisedCase
from plt.pipeline.filters.base import FilterResult
from plt.utils.logging import get_logger

__all__ = ["MatchReport", "default_report_path"]

log = get_logger(__name__)

#: Terms listed per document. A full text can match a great many; the report is meant to be
#: read, and the complete set is in ``keyword_match`` for anything that was persisted.
_TERM_LIMIT = 50


def default_report_path(directory: Path, jurisdiction_code: str, connector: str) -> Path:
    """Build the path a run writes its report to when the caller named none.

    Args:
        directory: Configured report directory (``PLT_PIPELINE_REPORT_DIR``).
        jurisdiction_code: Jurisdiction the run covers.
        connector: Connector name.

    Returns:
        A path of the form ``<directory>/<code>-<connector>-<timestamp>.jsonl``. The
        components are sanitised, so a code taken from the command line cannot escape the
        directory.
    """
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    safe_code = _sanitise(jurisdiction_code) or "unknown"
    safe_connector = _sanitise(connector) or "connector"
    return directory / f"{safe_code}-{safe_connector}-{stamp}.jsonl"


def _sanitise(value: str) -> str:
    """Reduce a value to characters that are safe in a file name.

    Args:
        value: The value to sanitise.

    Returns:
        The value with everything but ASCII letters, digits, ``-`` and ``_`` removed, so no
        path separator or traversal sequence survives.
    """
    return "".join(character for character in value if character.isalnum() or character in "-_")


class MatchReport:
    """A JSON Lines report of what the filter chain decided, written as the run goes.

    Use it as a context manager; the file is opened on entry and closed in a ``finally``, so
    an interrupted run leaves a complete file behind rather than a locked handle.
    """

    def __init__(self, path: Path, *, jurisdiction_code: str, connector: str) -> None:
        """Prepare a report.

        Args:
            path: File to write. Parent directories are created on open.
            jurisdiction_code: Jurisdiction the run covers, recorded in the header line.
            connector: Connector name, recorded in the header line.
        """
        self._path = path
        self._jurisdiction_code = jurisdiction_code
        self._connector = connector
        self._handle: TextIO | None = None
        self._written = 0

    @property
    def path(self) -> Path:
        """Return the file the report is written to."""
        return self._path

    @property
    def written(self) -> int:
        """Return how many document lines have been written."""
        return self._written

    def open(self) -> Self:
        """Create the directory, open the file and write the header line.

        Returns:
            The report, ready to record documents.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("w", encoding="utf-8")
        self._write(
            {
                "type": "run",
                "jurisdiction": self._jurisdiction_code,
                "connector": self._connector,
                "started_at": utcnow().isoformat(),
            }
        )
        log.info("writing match report", extra={"context": {"path": str(self._path)}})
        return self

    def record(
        self,
        case: NormalisedCase,
        result: FilterResult,
        *,
        action: str,
        content_hash: str | None = None,
        modified_at: datetime | None = None,
    ) -> None:
        """Append one document's verdict.

        Args:
            case: The normalised case that was judged.
            result: The verdict of the filter chain.
            action: What the run would have done — ``insert``, ``update`` or ``skip``.
            content_hash: Revision fingerprint the run resolved, if it got that far.
            modified_at: Source modification timestamp, for locating the document again.
        """
        self._write(
            {
                "type": "case",
                "jurisdiction": case.jurisdiction_code,
                "source_id": case.source_id,
                "title": case.title,
                "decision_date": (
                    case.decision_date.isoformat() if case.decision_date is not None else None
                ),
                "modified_at": modified_at.isoformat() if modified_at is not None else None,
                "language": case.language,
                "text_languages": list(case.text_languages),
                "content_hash": content_hash,
                "action": action,
                "passed": result.passed,
                "score": round(result.score, 4),
                "stage": result.stage,
                "reason": result.reason,
                "terms": [
                    {
                        "term_id": match.term_id,
                        "field": match.field,
                        "weight_applied": round(match.weight_applied, 4),
                        "occurrences": match.occurrences,
                        "gated": match.gated,
                        "matched_text": match.matched_text,
                        "snippet": match.snippet,
                    }
                    for match in result.matches[:_TERM_LIMIT]
                ],
                "terms_omitted": max(0, len(result.matches) - _TERM_LIMIT),
            }
        )
        self._written += 1

    def _write(self, payload: dict[str, Any]) -> None:
        """Write one JSON object as a line and flush it.

        Args:
            payload: The object to serialise.

        Raises:
            RuntimeError: If the report has not been opened.
        """
        if self._handle is None:
            message = "the match report has to be opened before anything can be written to it"
            raise RuntimeError(message)
        self._handle.write(json.dumps(payload, ensure_ascii=False, default=str))
        self._handle.write("\n")
        self._handle.flush()

    def close(self) -> None:
        """Close the file, if it is open."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> Self:
        """Open the report on entering the context."""
        return self.open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the report on leaving the context."""
        del exc_type, exc, traceback
        self.close()
