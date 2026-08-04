"""Checkpoints: where the next run starts, and what may move that position.

The weekly job runs with no window and lets the stored checkpoint supply one
(``docs/architecture.md`` section 7), so a position that moved too far silently skips case
law. These tests pin the monotonicity rule and the round trip through ``ingest_checkpoint``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from plt.pipeline.checkpoint import (
    Checkpoint,
    clear_checkpoint,
    read_checkpoint,
    resolve_since,
    write_checkpoint,
)

EARLIER = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LATER = EARLIER + timedelta(days=7)


def position(**overrides: object) -> Checkpoint:
    """Build a checkpoint with sensible defaults."""
    fields: dict[str, object] = {"connector": "fake", "jurisdiction_code": "NL"}
    fields.update(overrides)
    return Checkpoint(**fields)  # type: ignore[arg-type]


def test_a_fresh_position_is_empty() -> None:
    assert position().is_empty


def test_advancing_keeps_the_latest_timestamp() -> None:
    advanced = position().advanced_to(modified_at=EARLIER).advanced_to(modified_at=LATER)

    assert advanced.last_modified_seen == LATER


def test_advancing_never_moves_backwards() -> None:
    """A source that yields slightly out of order must not rewind a year of case law."""
    advanced = position().advanced_to(modified_at=LATER).advanced_to(modified_at=EARLIER)

    assert advanced.last_modified_seen == LATER


def test_the_cursor_and_identifier_follow_the_most_recent_document() -> None:
    advanced = (
        position()
        .advanced_to(cursor="page:1", source_id="a")
        .advanced_to(cursor="page:2", source_id="b")
    )

    assert advanced.last_cursor == "page:2"
    assert advanced.last_source_id == "b"


def test_advancing_over_a_document_without_a_timestamp_keeps_the_position() -> None:
    advanced = position().advanced_to(modified_at=LATER).advanced_to(source_id="b")

    assert advanced.last_modified_seen == LATER


def test_the_json_form_is_serialisable() -> None:
    payload = position(last_modified_seen=LATER, last_cursor="page:2").as_dict()

    assert payload == {
        "connector": "fake",
        "jurisdiction_code": "NL",
        "last_modified_seen": LATER.isoformat(),
        "last_cursor": "page:2",
        "last_source_id": None,
    }


def test_reading_an_unknown_connector_returns_nothing(seeded_session: Session) -> None:
    assert read_checkpoint(seeded_session, "fake") is None


def test_a_position_survives_a_round_trip(seeded_session: Session) -> None:
    write_checkpoint(
        seeded_session,
        position(last_modified_seen=LATER, last_cursor="page:2", last_source_id="b"),
    )
    seeded_session.flush()

    stored = read_checkpoint(seeded_session, "fake")

    assert stored is not None
    assert stored.last_modified_seen == LATER
    assert stored.last_cursor == "page:2"
    assert stored.last_source_id == "b"
    assert stored.updated_at is not None


def test_writing_twice_updates_the_one_row(seeded_session: Session) -> None:
    write_checkpoint(seeded_session, position(last_modified_seen=EARLIER))
    seeded_session.flush()
    write_checkpoint(seeded_session, position(last_modified_seen=LATER))
    seeded_session.flush()

    stored = read_checkpoint(seeded_session, "fake")

    assert stored is not None
    assert stored.last_modified_seen == LATER


def test_clearing_removes_the_position(seeded_session: Session) -> None:
    write_checkpoint(seeded_session, position(last_modified_seen=LATER))
    seeded_session.flush()

    assert clear_checkpoint(seeded_session, "fake") is True
    seeded_session.flush()
    assert read_checkpoint(seeded_session, "fake") is None
    assert clear_checkpoint(seeded_session, "fake") is False


def test_an_explicit_window_beats_the_checkpoint() -> None:
    assert resolve_since(EARLIER, position(last_modified_seen=LATER)) == EARLIER


def test_the_checkpoint_supplies_the_window_when_none_was_given() -> None:
    assert resolve_since(None, position(last_modified_seen=LATER)) == LATER


def test_a_first_run_has_no_lower_bound() -> None:
    assert resolve_since(None, None) is None
