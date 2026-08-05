"""Telling the administrator that the review queue has gained items.

Core document section 2.7 buys precision back by review, and review only happens if somebody
is told. These tests hold the three properties that makes safe: it goes to one configured
address, it says nothing publicly, and it reports only items that still need a decision.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from plt.config import Settings
from plt.db.models import (
    Case,
    CaseReview,
    KeywordMatch,
    ReviewDecision,
    ReviewStatus,
)
from plt.notifications.reviews import notify_new_reviews
from tests.conftest import RecordingMailer, build_settings

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
RUN_STARTED = NOW - timedelta(minutes=30)
ADMIN = "content-manager@example.org"


@pytest.fixture
def admin_settings(settings: Settings) -> Settings:
    """Return settings with an administrator configured."""
    return settings.model_copy(update={"admin_email": ADMIN})


def flag(
    session: Session,
    source_id: str,
    *,
    flagged_at: datetime,
    status: ReviewStatus = ReviewStatus.PENDING,
    decision: ReviewDecision | None = None,
) -> CaseReview:
    """Store one flagged case with a review row in a chosen state.

    Args:
        session: Open session.
        source_id: Identifier for the case.
        flagged_at: When the flag was raised.
        status: Where the item stands in the queue.
        decision: The standing decision, if one was taken.

    Returns:
        The review row.
    """
    case = Case(
        jurisdiction_code="NL",
        source_id=source_id,
        source_system="rechtspraak",
        title=f"Borderline case {source_id}",
        needs_review=True,
        filter_score=3.5,
    )
    case.keyword_matches.append(KeywordMatch(term_id="nl-001", term="drift", field="full_text"))
    case.review = CaseReview(
        status=status,
        score=3.5,
        min_score=3.0,
        band_ceiling=6.0,
        list_version="1.1.0",
        reason="score 3.5 reaches min_score 3, inside the review band [3, 6)",
        flagged_at=flagged_at,
        decision=decision,
    )
    session.add(case)
    session.flush()
    return case.review


def test_the_notice_lists_what_a_reviewer_needs_to_act(
    seeded_session: Session, admin_settings: Settings, mailer: RecordingMailer
) -> None:
    flag(seeded_session, "ECLI:NL:RVS:2026:1", flagged_at=NOW)

    total = notify_new_reviews(seeded_session, admin_settings, mailer, since=RUN_STARTED)

    assert total == 1
    message = mailer.sent[0]
    assert message.to == ADMIN
    assert "ECLI:NL:RVS:2026:1" in message.body
    assert "score 3.5" in message.body
    assert "1.1.0" in message.body
    assert admin_settings.site_url("/cases/NL/ECLI%3ANL%3ARVS%3A2026%3A1") in message.body


def test_one_message_covers_the_whole_run(
    seeded_session: Session, admin_settings: Settings, mailer: RecordingMailer
) -> None:
    # An alarm that fires forty times is one nobody reads.
    for index in range(12):
        flag(seeded_session, f"ECLI:NL:RVS:2026:{index}", flagged_at=NOW)

    total = notify_new_reviews(seeded_session, admin_settings, mailer, since=RUN_STARTED)

    assert total == 12
    assert len(mailer.sent) == 1


def test_a_long_queue_is_bounded_and_says_so(
    seeded_session: Session, admin_settings: Settings, mailer: RecordingMailer
) -> None:
    capped = admin_settings.model_copy(update={"admin_notice_max_items": 3})
    for index in range(5):
        flag(seeded_session, f"ECLI:NL:RVS:2026:{index}", flagged_at=NOW)

    total = notify_new_reviews(seeded_session, capped, mailer, since=RUN_STARTED)

    assert total == 5
    assert "3 of 5" in mailer.sent[0].body


def test_nothing_is_sent_when_the_run_flagged_nothing(
    seeded_session: Session, admin_settings: Settings, mailer: RecordingMailer
) -> None:
    flag(seeded_session, "ECLI:NL:RVS:2025:1", flagged_at=NOW - timedelta(days=30))

    total = notify_new_reviews(seeded_session, admin_settings, mailer, since=RUN_STARTED)

    assert total == 0
    assert mailer.sent == []


def test_an_item_already_decided_is_not_reported_again(
    seeded_session: Session, admin_settings: Settings, mailer: RecordingMailer
) -> None:
    flag(
        seeded_session,
        "ECLI:NL:RVS:2026:2",
        flagged_at=NOW,
        status=ReviewStatus.CONFIRMED,
        decision=ReviewDecision.CONFIRMED,
    )
    flag(seeded_session, "ECLI:NL:RVS:2026:3", flagged_at=NOW, status=ReviewStatus.WITHDRAWN)

    total = notify_new_reviews(seeded_session, admin_settings, mailer, since=RUN_STARTED)

    assert total == 0
    assert mailer.sent == []


def test_no_administrator_configured_means_no_message(
    seeded_session: Session, settings: Settings, mailer: RecordingMailer
) -> None:
    # The queue is unaffected either way: the flags are in the database whatever happens here.
    flag(seeded_session, "ECLI:NL:RVS:2026:4", flagged_at=NOW)

    total = notify_new_reviews(seeded_session, settings, mailer, since=RUN_STARTED)

    assert settings.admin_email is None
    assert total == 0
    assert mailer.sent == []


def test_the_notice_is_not_a_mailing_list_message(
    seeded_session: Session, admin_settings: Settings, mailer: RecordingMailer
) -> None:
    # Operational mail to the operator: no unsubscribe token, because there is no
    # subscription and nothing a stranger could cancel.
    flag(seeded_session, "ECLI:NL:RVS:2026:5", flagged_at=NOW)

    notify_new_reviews(seeded_session, admin_settings, mailer, since=RUN_STARTED)

    message = mailer.sent[0]
    assert "List-Unsubscribe" not in message.headers
    assert "/unsubscribe" not in message.body


def test_the_settings_default_keeps_the_queue_silent() -> None:
    assert build_settings().admin_email is None
