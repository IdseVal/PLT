"""Retention of subscriber rows, and the statistics that survive the address.

Two things are held here. First, that an unset period means **not enforced** rather than an
implied number: issue #75 is the Law group's to answer and this project must not answer it by
default. Second, that the record core document section 2.12 says the project keeps — dates,
tenure, digests sent — really is computable once the address is gone, because a decision to
drop personal data is only defensible if what was promised in its place actually works.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from plt.config import Settings
from plt.db.models import Subscriber, SubscriberStatus
from plt.notifications.pseudonyms import address_digest
from plt.notifications.retention import run_purge
from tests.conftest import build_settings

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


@pytest.fixture
def factory(db_session: Session) -> sessionmaker[Session]:
    """Return a session factory on the same temporary database as ``db_session``."""
    bind = db_session.get_bind()
    return sessionmaker(bind=bind, expire_on_commit=False)


def add_withdrawn(
    session: Session, email: str, *, unsubscribed_at: datetime, settings: Settings
) -> Subscriber:
    """Store one row in the state an unsubscribe leaves behind.

    Args:
        session: Open session.
        email: The address the row used to hold, which it no longer does.
        unsubscribed_at: When the subscription ended.
        settings: Settings supplying the pepper.

    Returns:
        The stored row.
    """
    subscriber = Subscriber(
        email=None,
        email_digest=address_digest(email, settings.address_pepper),
        status=SubscriberStatus.UNSUBSCRIBED,
        token_seed=f"seed-{email}",
        created_at=unsubscribed_at - timedelta(days=400),
        confirmed_at=unsubscribed_at - timedelta(days=399),
        unsubscribed_at=unsubscribed_at,
        digest_count=57,
    )
    session.add(subscriber)
    return subscriber


def add_unconfirmed(session: Session, email: str, *, created_at: datetime) -> Subscriber:
    """Store one address that was submitted and never confirmed.

    Args:
        session: Open session.
        email: The address.
        created_at: When it was submitted.

    Returns:
        The stored row.
    """
    subscriber = Subscriber(
        email=email,
        status=SubscriberStatus.PENDING,
        token_seed=f"seed-{email}",
        created_at=created_at,
        notice_sent_at=created_at,
    )
    session.add(subscriber)
    return subscriber


class TestAnUnsetPeriodIsNotAPolicy:
    """Issue #75 is open, and this code must not close it with a default."""

    def test_nothing_is_purged_when_neither_period_is_configured(
        self, db_session: Session, settings: Settings, factory: sessionmaker[Session]
    ) -> None:
        add_withdrawn(
            db_session,
            "gone@example.org",
            unsubscribed_at=NOW - timedelta(days=3650),
            settings=settings,
        )
        add_unconfirmed(db_session, "never@example.org", created_at=NOW - timedelta(days=3650))
        db_session.commit()

        report = run_purge(now=NOW, settings=settings, session_factory=factory)

        assert (report.digests_dropped, report.unconfirmed_deleted) == (0, 0)
        assert not report.retention_enforced
        assert not report.expiry_enforced
        db_session.expire_all()
        assert db_session.scalar(select(func.count()).select_from(Subscriber)) == 2

    def test_a_blank_assignment_reads_as_unset(self) -> None:
        """``PLT_SUBSCRIBER_RETENTION_DAYS=`` is how an operator writes "not decided"."""
        settings = build_settings(
            subscriber_retention_days="",
            subscriber_unconfirmed_expiry_days="  ",
            subscription_address_pepper="",
        )

        assert settings.subscriber_retention_days is None
        assert settings.subscriber_unconfirmed_expiry_days is None
        # An empty pepper would otherwise key every digest with nothing at all.
        assert settings.subscription_address_pepper is None

    def test_the_report_tells_no_rule_apart_from_nothing_matched(
        self, db_session: Session, settings: Settings, factory: sessionmaker[Session]
    ) -> None:
        """The two look identical as a count and mean opposite things."""
        del db_session
        unset = run_purge(now=NOW, settings=settings, session_factory=factory)
        configured = run_purge(
            now=NOW,
            settings=build_settings(
                database_url=settings.database_url,
                subscriber_retention_days=30,
                subscriber_unconfirmed_expiry_days=7,
            ),
            session_factory=factory,
        )

        assert "not configured" in unset.summary()
        assert "not configured" not in configured.summary()


class TestWhatEachRuleDoes:
    """One drops a digest and keeps the row; the other deletes the row."""

    def test_the_retention_horizon_drops_the_digest_and_keeps_the_record(
        self, db_session: Session, settings: Settings, factory: sessionmaker[Session]
    ) -> None:
        configured = build_settings(
            database_url=settings.database_url, subscriber_retention_days=365
        )
        old = add_withdrawn(
            db_session,
            "old@example.org",
            unsubscribed_at=NOW - timedelta(days=400),
            settings=configured,
        )
        recent = add_withdrawn(
            db_session,
            "recent@example.org",
            unsubscribed_at=NOW - timedelta(days=10),
            settings=configured,
        )
        db_session.commit()
        old_id, recent_id = old.id, recent.id

        report = run_purge(now=NOW, settings=configured, session_factory=factory)

        assert report.digests_dropped == 1
        db_session.expire_all()
        expired = db_session.get(Subscriber, old_id)
        assert expired is not None
        assert expired.email_digest is None
        # The record survives the loss of the pseudonym: this is what "reduces to a pure
        # counter" means.
        assert expired.digest_count == 57
        assert expired.unsubscribed_at is not None
        kept = db_session.get(Subscriber, recent_id)
        assert kept is not None
        assert kept.email_digest is not None

    def test_an_address_that_never_confirmed_is_deleted_outright(
        self, db_session: Session, settings: Settings, factory: sessionmaker[Session]
    ) -> None:
        """It consented to nothing, so there is no record worth keeping."""
        configured = build_settings(
            database_url=settings.database_url, subscriber_unconfirmed_expiry_days=7
        )
        add_unconfirmed(db_session, "stale@example.org", created_at=NOW - timedelta(days=30))
        add_unconfirmed(db_session, "fresh@example.org", created_at=NOW - timedelta(days=1))
        db_session.commit()

        report = run_purge(now=NOW, settings=configured, session_factory=factory)

        assert report.unconfirmed_deleted == 1
        db_session.expire_all()
        remaining = db_session.scalars(select(Subscriber.email)).all()
        assert list(remaining) == ["fresh@example.org"]

    def test_a_confirmed_subscription_is_never_touched(
        self, db_session: Session, settings: Settings, factory: sessionmaker[Session]
    ) -> None:
        configured = build_settings(
            database_url=settings.database_url,
            subscriber_retention_days=1,
            subscriber_unconfirmed_expiry_days=1,
        )
        db_session.add(
            Subscriber(
                email="live@example.org",
                status=SubscriberStatus.CONFIRMED,
                token_seed="seed-live",
                created_at=NOW - timedelta(days=3650),
                confirmed_at=NOW - timedelta(days=3649),
            )
        )
        db_session.commit()

        run_purge(now=NOW, settings=configured, session_factory=factory)

        db_session.expire_all()
        assert db_session.scalars(select(Subscriber.email)).all() == ["live@example.org"]


class TestStatisticsSurviveTheAddress:
    """Core document 2.12: statistics are computed from the row, not from the address."""

    def test_tenure_and_volume_are_computable_with_no_address_in_the_table(
        self, db_session: Session, settings: Settings
    ) -> None:
        add_withdrawn(
            db_session,
            "one@example.org",
            unsubscribed_at=datetime(2026, 7, 1, tzinfo=UTC),
            settings=settings,
        )
        add_withdrawn(
            db_session,
            "two@example.org",
            unsubscribed_at=datetime(2026, 6, 1, tzinfo=UTC),
            settings=settings,
        )
        db_session.commit()

        rows = list(db_session.scalars(select(Subscriber)))

        assert all(row.email is None for row in rows), "the premise: no address is held"
        # Everything the decision promised the project would keep, from the row alone.
        assert sum(row.digest_count for row in rows) == 114
        assert [
            (row.unsubscribed_at - row.confirmed_at).days
            for row in rows
            if row.unsubscribed_at is not None and row.confirmed_at is not None
        ] == [399, 399]
        assert (
            db_session.scalar(
                select(func.count()).where(Subscriber.status == SubscriberStatus.UNSUBSCRIBED)
            )
            == 2
        )
