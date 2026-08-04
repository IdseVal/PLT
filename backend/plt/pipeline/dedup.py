"""Deduplication on source identifier and content hash.

The deduplication key is ``UNIQUE (jurisdiction_code, source_id)`` — an ECLI for the
Netherlands, a CELEX number for the EU — enforced by the database rather than by application
code. On a re-run the content hash decides what happens (``docs/architecture.md`` section 3):
identical touches ``last_seen_at`` only, different updates the row in place and bumps
``revision``. A second row for the same source identifier is never inserted.

The check runs **twice**, which is the whole point of splitting discovery from fetching:

1. :func:`decide_before_fetch` runs on a :class:`~plt.pipeline.base.Candidate`, so a
   document whose revision fingerprint is already stored is never downloaded again. A weekly
   run over a source that publishes a rolling window therefore costs one cheap query per
   known document instead of one full judgment.
2. :func:`decide_after_fetch` runs once the document is in hand, for the connectors and the
   documents where discovery could not supply a fingerprint. It is what makes "run the same
   window twice, insert nothing the second time" hold for every source.

Hashes come from :func:`content_hash`, which is SHA-256 over a canonical projection of the
normalised record: exactly 64 hexadecimal characters, which is the width of the
``case.content_hash`` column. The projection covers the fields a genuine upstream revision
would change and nothing that varies between runs, so a re-fetch of an untouched document
hashes to the same value.
"""

from __future__ import annotations

import enum
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from plt.db.repositories import CaseFingerprint, get_case_fingerprint
from plt.pipeline.base import Candidate, NormalisedCase

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = [
    "DedupAction",
    "DedupDecision",
    "content_hash",
    "decide_after_fetch",
    "decide_before_fetch",
    "fingerprint",
    "resolve_content_hash",
]

#: Separator between the parts of a hashed projection. A control character, so it cannot
#: occur in a field value and let two different records collide by concatenation.
_FIELD_SEPARATOR = "\x1f"


class DedupAction(enum.StrEnum):
    """What deduplication concluded about one candidate."""

    #: Not in the database: fetch it, filter it, insert it if it passes.
    INSERT = "insert"
    #: Stored already, and the content may have changed: fetch it and compare.
    UPDATE = "update"
    #: Stored already and unchanged: touch ``last_seen_at`` and move on.
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class DedupDecision:
    """The verdict on one candidate, with the fingerprint that produced it.

    Attributes:
        action: What the runner should do next.
        reason: Human-readable explanation, logged and reported.
        fingerprint: The stored fingerprint, or ``None`` when the case is unknown. Carried
            so the runner does not have to query twice.
    """

    action: DedupAction
    reason: str
    fingerprint: CaseFingerprint | None = None

    @property
    def needs_fetch(self) -> bool:
        """Return whether the document still has to be downloaded."""
        return self.action is not DedupAction.SKIP

    @property
    def case_id(self) -> int | None:
        """Return the id of the stored case, or ``None`` if there is none."""
        return self.fingerprint.id if self.fingerprint is not None else None


def content_hash(parts: Iterable[str | None]) -> str:
    """Hash a sequence of field values into a revision fingerprint.

    Args:
        parts: Field values in a fixed order. ``None`` is distinguished from the empty
            string, so a field cleared upstream still counts as a revision.

    Returns:
        The SHA-256 hex digest, 64 characters — the width of ``case.content_hash``.
    """
    digest = hashlib.sha256()
    separator = _FIELD_SEPARATOR.encode("utf-8")
    for part in parts:
        digest.update(b"\x00" if part is None else part.encode("utf-8"))
        digest.update(separator)
    return digest.hexdigest()


def fingerprint(case: NormalisedCase) -> str:
    """Compute the revision fingerprint of a normalised case.

    The projection covers what an upstream revision would change — the identifiers, the
    editorial fields, the dates, the classification and every document's text — and excludes
    everything that varies between runs, such as retrieval timestamps. ``source_metadata`` is
    excluded deliberately: sources put volatile counters and request echoes in there, and a
    hash that moved on every run would rewrite the whole corpus weekly.

    Args:
        case: The normalised case to fingerprint.

    Returns:
        A 64-character hex digest.
    """
    parts: list[str | None] = [
        case.jurisdiction_code,
        case.source_id,
        case.source_system,
        case.title,
        case.abstract,
        case.subject,
        case.decision_date.isoformat() if case.decision_date is not None else None,
        case.filing_date.isoformat() if case.filing_date is not None else None,
        case.publication_date.isoformat() if case.publication_date is not None else None,
        "|".join(case.case_numbers),
        case.language,
        case.law_domain.value if case.law_domain is not None else None,
        case.law_subfield,
        case.procedure_type,
        case.outcome,
        case.source_url,
        case.court.source_identifier if case.court is not None else None,
    ]
    for document in case.documents:
        parts.extend(
            (
                document.doc_type.value,
                document.language,
                document.media_format,
                document.full_text,
            )
        )
    for party in case.parties:
        parts.extend((party.name, party.role.value))
    for citation in case.citations:
        parts.extend((citation.target_identifier, citation.citation_type))
    return content_hash(parts)


def resolve_content_hash(candidate: Candidate, case: NormalisedCase) -> str:
    """Decide which hash identifies this revision of a document.

    Three sources, in order of authority:

    1. the hash the connector set on the normalised case, when the source publishes a
       revision identifier of its own;
    2. the hash discovery supplied on the candidate — using it keeps the pre-fetch check and
       the stored value in the same hash space, which is what makes skipping a fetch
       possible at all;
    3. a fingerprint of the normalised content, for sources that expose neither.

    Args:
        candidate: The candidate the document was discovered as.
        case: The normalised case.

    Returns:
        The hash to store on ``case.content_hash``.
    """
    if case.content_hash:
        return case.content_hash
    if candidate.content_hash:
        return candidate.content_hash
    return fingerprint(case)


def decide_before_fetch(session: Session, candidate: Candidate) -> DedupDecision:
    """Decide what to do with a candidate before spending a request on it.

    Args:
        session: Open database session, used read-only.
        candidate: The candidate discovery yielded.

    Returns:
        The decision. :attr:`DedupAction.SKIP` is only ever returned when discovery supplied
        a fingerprint that matches the stored one; without one the document has to be
        fetched before anything can be concluded.
    """
    stored = get_case_fingerprint(session, candidate.jurisdiction_code, candidate.source_id)
    if stored is None:
        return DedupDecision(action=DedupAction.INSERT, reason="not seen before")
    if candidate.content_hash and stored.content_hash == candidate.content_hash:
        return DedupDecision(
            action=DedupAction.SKIP,
            reason="unchanged since the last run (discovery fingerprint matches)",
            fingerprint=stored,
        )
    reason = (
        "revision fingerprint changed at the source"
        if candidate.content_hash
        else "already stored; discovery exposes no fingerprint, so the document is re-fetched"
    )
    return DedupDecision(action=DedupAction.UPDATE, reason=reason, fingerprint=stored)


def decide_after_fetch(before: DedupDecision, resolved_hash: str) -> DedupDecision:
    """Refine the decision once the document has been fetched and normalised.

    Args:
        before: The decision :func:`decide_before_fetch` returned for this candidate.
        resolved_hash: The hash of the document as fetched, from
            :func:`resolve_content_hash`.

    Returns:
        The final decision: skip when the stored hash is unchanged, update when it differs,
        insert when the case is unknown.
    """
    stored = before.fingerprint
    if stored is None:
        return DedupDecision(action=DedupAction.INSERT, reason="not seen before")
    if stored.content_hash == resolved_hash:
        return DedupDecision(
            action=DedupAction.SKIP,
            reason="unchanged since the last run (content hash matches)",
            fingerprint=stored,
        )
    return DedupDecision(
        action=DedupAction.UPDATE,
        reason=f"content changed upstream; revision {stored.revision} superseded",
        fingerprint=stored,
    )
