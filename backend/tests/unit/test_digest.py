"""The weekly digest.

What is being held here is that the send is safe to run unattended: it reaches only addresses
that asked for it, it never sends the same window twice, an interruption resumes rather than
restarting, one bad recipient does not end the run, and a week with no cases produces silence
rather than an empty message.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from plt.config import Settings
from plt.db.models import Case, Subscriber, SubscriberStatus
from plt.notifications.digest import DigestReport, run_digest
from plt.notifications.mailer import MailError, Message
from plt.notifications.messages import unsubscribe_url
from plt.notifications.pseudonyms import address_digest
from plt.utils.shutdown import StopRequest
from tests.conftest import RecordingMailer

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
WINDOW_START = NOW - timedelta(days=7)

#: Pepper for the withdrawn rows these tests build directly. Any value will do: nothing here
#: recognises a returning address, it only needs a row shaped as an unsubscribe leaves it.
PEPPER = b"a-test-pepper-value"


@pytest.fixture
def factory(db_session: Session) -> sessionmaker[Session]:
    """Return a session factory on the same temporary database as ``db_session``."""
    bind = db_session.get_bind()
    return sessionmaker(bind=bind, expire_on_commit=False)


def add_case(session: Session, source_id: str, *, first_seen: datetime) -> Case:
    """Store one published case with a chosen ``first_seen_at``.

    Args:
        session: Open session.
        source_id: Identifier for the case.
        first_seen: When the tracker first saw it.

    Returns:
        The stored case.
    """
    case = Case(
        jurisdiction_code="NL",
        source_id=source_id,
        source_system="rechtspraak",
        title=f"Case {source_id}",
        first_seen_at=first_seen,
        last_seen_at=first_seen,
    )
    session.add(case)
    return case


def add_subscriber(
    session: Session,
    email: str,
    *,
    status: SubscriberStatus = SubscriberStatus.CONFIRMED,
    last_digest_at: datetime | None = None,
) -> Subscriber:
    """Store one subscriber in a chosen state.

    An unsubscribed row holds no address — the schema forbids it, because unsubscribing
    replaces the address with a keyed digest (core document 2.12) — so this builds that state
    as the lifecycle would leave it rather than as a row that could never exist.

    Args:
        session: Open session.
        email: The address, or the address the row used to hold when ``status`` is
            ``unsubscribed``.
        status: Lifecycle state.
        last_digest_at: The position of the last digest this address was sent.

    Returns:
        The stored subscriber.
    """
    withdrawn = status is SubscriberStatus.UNSUBSCRIBED
    subscriber = Subscriber(
        email=None if withdrawn else email,
        email_digest=address_digest(email, PEPPER) if withdrawn else None,
        status=status,
        token_seed=f"seed-for-{email.split('@')[0]}",
        confirmed_at=NOW - timedelta(days=30) if status is SubscriberStatus.CONFIRMED else None,
        unsubscribed_at=NOW - timedelta(days=1) if withdrawn else None,
        last_digest_at=last_digest_at,
    )
    session.add(subscriber)
    return subscriber


@pytest.fixture
def corpus(seeded_session: Session) -> Session:
    """Commit one case inside the window and one well before it."""
    add_case(seeded_session, "ECLI:NL:RVS:2026:1", first_seen=NOW - timedelta(days=1))
    add_case(seeded_session, "ECLI:NL:RVS:2025:9", first_seen=NOW - timedelta(days=90))
    seeded_session.commit()
    return seeded_session


def digest(
    settings: Settings,
    factory: sessionmaker[Session],
    mailer: RecordingMailer,
    *,
    since: datetime = WINDOW_START,
    until: datetime = NOW,
    dry_run: bool = False,
) -> DigestReport:
    """Run one digest over the fixed window.

    Args:
        settings: Validated settings.
        factory: Session factory.
        mailer: Recording backend.
        since: Start of the window.
        until: End of the window.
        dry_run: Whether to render without sending.

    Returns:
        The digest report.
    """
    return run_digest(
        since=since,
        until=until,
        dry_run=dry_run,
        settings=settings,
        session_factory=factory,
        mailer=mailer,
    )


class TestWhoIsWrittenTo:
    """Only confirmed addresses, and each exactly once per window."""

    def test_only_a_confirmed_address_receives_a_digest(
        self,
        corpus: Session,
        settings: Settings,
        factory: sessionmaker[Session],
        mailer: RecordingMailer,
    ) -> None:
        add_subscriber(corpus, "confirmed@example.org")
        add_subscriber(corpus, "pending@example.org", status=SubscriberStatus.PENDING)
        add_subscriber(corpus, "gone@example.org", status=SubscriberStatus.UNSUBSCRIBED)
        corpus.commit()

        report = digest(settings, factory, mailer)

        assert mailer.recipients == ["confirmed@example.org"]
        assert report.sent == 1

    def test_one_message_per_recipient_so_the_list_is_not_disclosed(
        self,
        corpus: Session,
        settings: Settings,
        factory: sessionmaker[Session],
        mailer: RecordingMailer,
    ) -> None:
        add_subscriber(corpus, "one@example.org")
        add_subscriber(corpus, "two@example.org")
        corpus.commit()

        digest(settings, factory, mailer)

        assert sorted(mailer.recipients) == ["one@example.org", "two@example.org"]
        assert all("," not in message.to for message in mailer.sent)

    def test_re_running_the_same_window_sends_to_nobody_twice(
        self,
        corpus: Session,
        settings: Settings,
        factory: sessionmaker[Session],
        mailer: RecordingMailer,
    ) -> None:
        add_subscriber(corpus, "reader@example.org")
        corpus.commit()

        digest(settings, factory, mailer)
        second = digest(settings, factory, mailer)

        assert len(mailer.sent) == 1
        assert second.sent == 0

    def test_an_address_added_after_a_send_is_reached_by_the_resumed_run(
        self,
        corpus: Session,
        settings: Settings,
        factory: sessionmaker[Session],
        mailer: RecordingMailer,
    ) -> None:
        add_subscriber(corpus, "early@example.org", last_digest_at=NOW)
        add_subscriber(corpus, "late@example.org")
        corpus.commit()

        digest(settings, factory, mailer)

        assert mailer.recipients == ["late@example.org"]


class TestWhatIsSent:
    """The window, the contents, and the way out."""

    def test_only_cases_first_seen_inside_the_window_are_listed(
        self,
        corpus: Session,
        settings: Settings,
        factory: sessionmaker[Session],
        mailer: RecordingMailer,
    ) -> None:
        add_subscriber(corpus, "reader@example.org")
        corpus.commit()

        report = digest(settings, factory, mailer)

        body = mailer.sent[0].body
        assert "ECLI:NL:RVS:2026:1" in body
        assert "ECLI:NL:RVS:2025:9" not in body
        assert report.case_count == 1

    def test_an_unpublished_case_is_not_announced(
        self,
        corpus: Session,
        settings: Settings,
        factory: sessionmaker[Session],
        mailer: RecordingMailer,
    ) -> None:
        # A case a reviewer rejected is hidden from /api/cases; announcing it in a digest
        # would republish it by email.
        withheld = add_case(corpus, "ECLI:NL:RVS:2026:7", first_seen=NOW - timedelta(hours=2))
        withheld.is_published = False
        add_subscriber(corpus, "reader@example.org")
        corpus.commit()

        digest(settings, factory, mailer)

        assert "ECLI:NL:RVS:2026:7" not in mailer.sent[0].body

    def test_every_message_carries_a_working_way_out(
        self,
        corpus: Session,
        settings: Settings,
        factory: sessionmaker[Session],
        mailer: RecordingMailer,
    ) -> None:
        subscriber = add_subscriber(corpus, "reader@example.org")
        corpus.commit()
        expected = unsubscribe_url(subscriber.token_seed, settings)

        digest(settings, factory, mailer)

        message: Message = mailer.sent[0]
        assert expected in message.body
        assert message.headers["List-Unsubscribe"] == f"<{expected}>"
        assert message.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    def test_nothing_in_a_message_reports_that_it_was_read(
        self,
        corpus: Session,
        settings: Settings,
        factory: sessionmaker[Session],
        mailer: RecordingMailer,
    ) -> None:
        add_subscriber(corpus, "reader@example.org")
        corpus.commit()

        digest(settings, factory, mailer)

        body = mailer.sent[0].body
        assert "<img" not in body
        assert "/track" not in body
        # Every link points at the site itself; none is a redirector carrying an identifier.
        for line in body.splitlines():
            candidate = line.strip()
            if candidate.startswith("http"):
                assert candidate.startswith(settings.site_base_url)

    def test_a_long_week_is_bounded_and_says_so(
        self,
        corpus: Session,
        settings: Settings,
        factory: sessionmaker[Session],
        mailer: RecordingMailer,
    ) -> None:
        for index in range(settings.digest_max_cases + 5):
            add_case(corpus, f"ECLI:NL:RVS:2026:100{index}", first_seen=NOW - timedelta(hours=3))
        add_subscriber(corpus, "reader@example.org")
        corpus.commit()

        report = digest(settings, factory, mailer)

        assert report.listed == settings.digest_max_cases
        assert str(report.case_count) in mailer.sent[0].body

    def test_a_quiet_week_sends_nothing_at_all(
        self,
        seeded_session: Session,
        settings: Settings,
        factory: sessionmaker[Session],
        mailer: RecordingMailer,
    ) -> None:
        add_subscriber(seeded_session, "reader@example.org")
        seeded_session.commit()

        report = digest(settings, factory, mailer)

        assert mailer.sent == []
        assert report.case_count == 0
        # ... and nobody's position moved, so next week's digest still reaches them.
        seeded_session.expire_all()
        assert seeded_session.scalars(select(Subscriber)).one().last_digest_at is None


class TestFailureAndInterruption:
    """A long unattended loop has to survive both."""

    def test_a_refused_recipient_does_not_end_the_run_and_is_retried(
        self,
        corpus: Session,
        settings: Settings,
        factory: sessionmaker[Session],
    ) -> None:
        add_subscriber(corpus, "broken@example.org")
        add_subscriber(corpus, "fine@example.org")
        corpus.commit()

        class OneBadAddress(RecordingMailer):
            """Refuses the first recipient and accepts the rest."""

            def send(self, message: Message) -> None:
                """Refuse one address and record the others.

                Args:
                    message: The message being sent.

                Raises:
                    MailError: For the address this backend refuses.
                """
                if message.to == "broken@example.org":
                    detail = "mailbox full"
                    raise MailError(detail)
                super().send(message)

        mailer = OneBadAddress()
        report = digest(settings, factory, mailer)

        assert mailer.recipients == ["fine@example.org"]
        assert report.failed == 1
        assert report.sent == 1
        corpus.expire_all()
        rows = {row.email: row.last_digest_at for row in corpus.scalars(select(Subscriber))}
        assert rows["broken@example.org"] is None
        assert rows["fine@example.org"] == NOW

    def test_an_inverted_window_is_refused_rather_than_mailed(
        self,
        corpus: Session,
        settings: Settings,
        factory: sessionmaker[Session],
        mailer: RecordingMailer,
    ) -> None:
        add_subscriber(corpus, "reader@example.org")
        corpus.commit()

        with pytest.raises(ValueError, match="window is empty"):
            digest(settings, factory, mailer, since=NOW, until=WINDOW_START)

        assert mailer.sent == []

    def test_a_dry_run_sends_nothing_and_moves_nobody(
        self,
        corpus: Session,
        settings: Settings,
        factory: sessionmaker[Session],
        mailer: RecordingMailer,
    ) -> None:
        add_subscriber(corpus, "reader@example.org")
        corpus.commit()

        report = digest(settings, factory, mailer, dry_run=True)

        assert mailer.sent == []
        assert report.sent == 1
        corpus.expire_all()
        assert corpus.scalars(select(Subscriber)).one().last_digest_at is None

    def test_a_shutdown_request_stops_after_the_message_in_flight(
        self,
        corpus: Session,
        settings: Settings,
        factory: sessionmaker[Session],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for index in range(4):
            add_subscriber(corpus, f"reader{index}@example.org")
        corpus.commit()

        stop = StopRequest()
        monkeypatch.setattr("plt.notifications.digest.StopRequest", lambda: stop)

        class StoppingMailer(RecordingMailer):
            """Asks the run to stop once the first message has gone."""

            def send(self, message: Message) -> None:
                """Record the message, then request a shutdown.

                Args:
                    message: The message being sent.
                """
                super().send(message)
                stop.request("the test asked for it")

        mailer = StoppingMailer()

        report = digest(settings, factory, mailer)

        assert len(mailer.sent) == 1
        assert report.interrupted is True
        # The message that did go is committed, so the resumed run does not repeat it.
        corpus.expire_all()
        stamped = [
            row.email
            for row in corpus.scalars(select(Subscriber))
            if row.last_digest_at is not None
        ]
        assert stamped == mailer.recipients
