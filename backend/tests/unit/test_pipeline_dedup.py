"""Deduplication: never a second row, never a needless download.

``docs/core-document.md`` section 2.6 makes the weekly run safely re-runnable. These tests
pin the two halves of that: the fingerprint is stable across a re-fetch and moves when the
content moves, and the pre-check skips a known unchanged document *before* it is fetched.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from plt.db.models import Case
from plt.pipeline.base import Candidate, NormalisedCase, NormalisedDocument
from plt.pipeline.dedup import (
    DedupAction,
    content_hash,
    decide_after_fetch,
    decide_before_fetch,
    fingerprint,
    resolve_content_hash,
)

MODIFIED = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def case(**overrides: object) -> NormalisedCase:
    """Build a normalised case with sensible defaults."""
    fields: dict[str, object] = {
        "source_id": "ECLI:NL:RBTEST:2026:1",
        "jurisdiction_code": "NL",
        "source_system": "fake",
        "title": "Uitspraak",
        "documents": (NormalisedDocument(language="nl", full_text="tekst"),),
    }
    fields.update(overrides)
    return NormalisedCase(**fields)  # type: ignore[arg-type]


def candidate(**overrides: object) -> Candidate:
    """Build a candidate with sensible defaults."""
    fields: dict[str, object] = {
        "source_id": "ECLI:NL:RBTEST:2026:1",
        "jurisdiction_code": "NL",
        "modified_at": MODIFIED,
    }
    fields.update(overrides)
    return Candidate(**fields)  # type: ignore[arg-type]


def store(session: Session, *, content: str | None) -> Case:
    """Insert a stored case with a given content hash."""
    row = Case(
        jurisdiction_code="NL",
        source_id="ECLI:NL:RBTEST:2026:1",
        source_system="fake",
        content_hash=content,
    )
    session.add(row)
    session.flush()
    return row


def test_the_hash_is_a_sha256_hex_digest() -> None:
    digest = content_hash(["a", None, "b"])

    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")


def test_a_missing_value_is_not_the_empty_string() -> None:
    assert content_hash([None]) != content_hash([""])


def test_field_boundaries_cannot_be_forged_by_concatenation() -> None:
    assert content_hash(["ab", "c"]) != content_hash(["a", "bc"])


def test_the_fingerprint_is_stable_across_an_identical_refetch() -> None:
    assert fingerprint(case()) == fingerprint(case())


def test_the_fingerprint_moves_when_the_text_changes() -> None:
    revised = case(documents=(NormalisedDocument(language="nl", full_text="andere tekst"),))

    assert fingerprint(case()) != fingerprint(revised)


def test_the_fingerprint_ignores_source_metadata() -> None:
    """A hash that moved every run would rewrite the whole corpus weekly.

    Sources echo request counters and retrieval timestamps into their payloads, so those
    stay out of the hashed projection.
    """
    noisy = case(source_metadata={"served_at": "2026-08-03T10:00:00Z"})

    assert fingerprint(case()) == fingerprint(noisy)


def test_a_connector_supplied_hash_wins() -> None:
    assert resolve_content_hash(candidate(), case(content_hash="upstream-7")) == "upstream-7"


def test_a_discovery_hash_is_carried_through_to_storage() -> None:
    """The pre-check and the stored value have to live in one hash space.

    Otherwise a document discovered as unchanged would look revised the moment it was
    fetched, and every run would rewrite it.
    """
    resolved = resolve_content_hash(candidate(content_hash="atom-updated-7"), case())

    assert resolved == "atom-updated-7"


def test_without_either_the_content_is_fingerprinted() -> None:
    assert resolve_content_hash(candidate(), case()) == fingerprint(case())


def test_an_unknown_document_is_fetched(seeded_session: Session) -> None:
    decision = decide_before_fetch(seeded_session, candidate())

    assert decision.action is DedupAction.INSERT
    assert decision.needs_fetch
    assert decision.case_id is None


def test_a_known_unchanged_document_is_skipped_before_the_fetch(seeded_session: Session) -> None:
    row = store(seeded_session, content="atom-updated-7")

    decision = decide_before_fetch(seeded_session, candidate(content_hash="atom-updated-7"))

    assert decision.action is DedupAction.SKIP
    assert not decision.needs_fetch
    assert decision.case_id == row.id


def test_a_changed_discovery_hash_is_fetched(seeded_session: Session) -> None:
    store(seeded_session, content="atom-updated-7")

    decision = decide_before_fetch(seeded_session, candidate(content_hash="atom-updated-8"))

    assert decision.action is DedupAction.UPDATE
    assert decision.needs_fetch


def test_without_a_discovery_hash_a_known_document_is_still_fetched(
    seeded_session: Session,
) -> None:
    """Nothing can be concluded before the document is in hand; the post-fetch check does it."""
    store(seeded_session, content="stored-hash")

    decision = decide_before_fetch(seeded_session, candidate())

    assert decision.action is DedupAction.UPDATE
    assert "no fingerprint" in decision.reason


def test_the_post_fetch_check_skips_an_unchanged_document(seeded_session: Session) -> None:
    row = store(seeded_session, content="stored-hash")
    before = decide_before_fetch(seeded_session, candidate())

    after = decide_after_fetch(before, "stored-hash")

    assert after.action is DedupAction.SKIP
    assert after.case_id == row.id


def test_the_post_fetch_check_updates_a_revised_document(seeded_session: Session) -> None:
    store(seeded_session, content="stored-hash")
    before = decide_before_fetch(seeded_session, candidate())

    after = decide_after_fetch(before, "different-hash")

    assert after.action is DedupAction.UPDATE
    assert "changed upstream" in after.reason


def test_the_post_fetch_check_inserts_an_unknown_document(seeded_session: Session) -> None:
    before = decide_before_fetch(seeded_session, candidate())

    after = decide_after_fetch(before, "any-hash")

    assert after.action is DedupAction.INSERT
