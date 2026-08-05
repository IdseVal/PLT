"""The public subscription endpoints.

These are the only unauthenticated, state-changing, mail-sending routes in the API, and the
list they write to is personal data. The tests are grouped by the property being defended
rather than by endpoint, because the properties are what a review of this feature will
attack: it must be impossible to learn who is on the list, impossible to use the form to
bombard somebody, and impossible to confirm or cancel a subscription without the token that
was sent to the address itself.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from plt.app import create_app
from plt.config import Settings
from plt.db.base import Base
from plt.db.models import Subscriber, SubscriberStatus
from plt.db.session import make_engine
from plt.extensions import dispose_database, limiter
from plt.notifications.messages import confirm_url, unsubscribe_url
from plt.notifications.tokens import TokenPurpose, issue_token
from tests.conftest import RecordingMailer, build_settings

SUBSCRIBE = "/api/subscriptions"
CONFIRM = "/api/subscriptions/confirm"
UNSUBSCRIBE = "/api/subscriptions/unsubscribe"
LINK = "/api/subscriptions/unsubscribe-link"


@pytest.fixture
def app(settings: Settings, db_session: Session) -> Iterator[Flask]:
    """Return an application sharing the temporary database the ORM fixtures created.

    Overrides the shared fixture: these routes write, so the schema has to exist, and a test
    reads back what a request stored through ``db_session``.
    """
    del db_session
    application = create_app(settings)
    try:
        yield application
    finally:
        dispose_database(application)


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> RecordingMailer:
    """Capture every message the endpoints send, whichever backend is configured."""
    recorder = RecordingMailer()
    monkeypatch.setattr("plt.api.subscriptions.build_mailer", lambda _settings: recorder)
    return recorder


@pytest.fixture
def known(db_session: Session) -> Subscriber:
    """Return a confirmed subscriber, committed so the application's session sees it."""
    subscriber = Subscriber(
        email="known@example.org",
        status=SubscriberStatus.CONFIRMED,
        token_seed="a-known-seed-value-0001",
        confirmed_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    db_session.add(subscriber)
    db_session.commit()
    return subscriber


def subscribers(session: Session) -> list[Subscriber]:
    """Read the table back, past whatever this session had already loaded.

    ``expire_all`` is not decoration: the request ran on the application's own session, and
    without it this session would hand back the instance it loaded before the request and the
    test would assert on the state it set up rather than on the state the endpoint left.

    Args:
        session: The test's own session, on the same file the application writes to.

    Returns:
        Every subscriber row, in primary-key order.
    """
    session.rollback()
    session.expire_all()
    return list(session.scalars(select(Subscriber).order_by(Subscriber.id)).all())


def token_for(subscriber: Subscriber, purpose: TokenPurpose, settings: Settings) -> str:
    """Return a live token for a subscriber, as a message would carry.

    Args:
        subscriber: The row concerned.
        purpose: What the token authorises.
        settings: The settings the application was built with.

    Returns:
        The token.
    """
    return issue_token(purpose, subscriber.token_seed, settings.token_secret)


class TestNoEnumeration:
    """Nothing may report whether an address is on the list."""

    def test_a_known_and_an_unknown_address_get_the_same_answer(
        self, client: FlaskClient, outbox: RecordingMailer, known: Subscriber
    ) -> None:
        del known, outbox
        first = client.post(SUBSCRIBE, json={"email": "known@example.org"})
        second = client.post(SUBSCRIBE, json={"email": "stranger@example.org"})

        assert first.status_code == second.status_code == HTTPStatus.ACCEPTED
        assert first.get_json() == second.get_json()

    def test_a_throttled_repeat_is_answered_like_a_first_submission(
        self, client: FlaskClient, outbox: RecordingMailer
    ) -> None:
        first = client.post(SUBSCRIBE, json={"email": "reader@example.org"})
        repeat = client.post(SUBSCRIBE, json={"email": "reader@example.org"})

        assert (first.status_code, first.get_json()) == (repeat.status_code, repeat.get_json())
        # ... and the repeat sent nothing, which is the anti-mail-bomb rule.
        assert len(outbox.sent) == 1

    def test_the_unsubscribe_link_request_answers_the_same_either_way(
        self, client: FlaskClient, outbox: RecordingMailer, known: Subscriber
    ) -> None:
        del known
        found = client.post(LINK, json={"email": "known@example.org"})
        missing = client.post(LINK, json={"email": "stranger@example.org"})

        assert found.status_code == missing.status_code == HTTPStatus.ACCEPTED
        assert found.get_json() == missing.get_json()
        # An address nobody knows is not written to: mailing it would be exactly the
        # unsolicited message this design exists to prevent.
        assert outbox.recipients == ["known@example.org"]

    def test_the_routes_are_post_only_so_no_address_reaches_a_url(self, app: Flask) -> None:
        rules = {
            str(rule): rule.methods or set()
            for rule in app.url_map.iter_rules()
            if str(rule).startswith("/api/subscriptions")
        }

        assert sorted(rules) == [SUBSCRIBE, CONFIRM, UNSUBSCRIBE, LINK]
        assert all("GET" not in methods for methods in rules.values())


class TestDoubleOptIn:
    """An address receives a digest only after it has confirmed."""

    def test_subscribing_stores_a_pending_row_and_sends_one_confirmation(
        self, client: FlaskClient, outbox: RecordingMailer, db_session: Session
    ) -> None:
        response = client.post(SUBSCRIBE, json={"email": "Reader@Example.ORG"})

        assert response.status_code == HTTPStatus.ACCEPTED
        stored = subscribers(db_session)
        assert len(stored) == 1
        assert stored[0].email == "reader@example.org"
        assert stored[0].status is SubscriberStatus.PENDING
        assert stored[0].confirmed_at is None
        assert outbox.recipients == ["reader@example.org"]

    def test_the_confirmation_link_confirms_and_is_idempotent(
        self, client: FlaskClient, outbox: RecordingMailer, settings: Settings, db_session: Session
    ) -> None:
        client.post(SUBSCRIBE, json={"email": "reader@example.org"})
        seed = subscribers(db_session)[0].token_seed
        assert confirm_url(seed, settings) in outbox.sent[0].body
        token = issue_token(TokenPurpose.CONFIRM, seed, settings.token_secret)

        first = client.post(CONFIRM, json={"token": token})
        second = client.post(CONFIRM, json={"token": token})

        assert first.status_code == second.status_code == HTTPStatus.OK
        assert first.get_json()["status"] == "confirmed"
        row = subscribers(db_session)[0]
        assert row.status is SubscriberStatus.CONFIRMED
        assert row.confirmed_at is not None

    def test_an_expired_confirmation_link_is_refused(
        self, client: FlaskClient, settings: Settings, db_session: Session, outbox: RecordingMailer
    ) -> None:
        del outbox
        stale = Subscriber(
            email="stale@example.org",
            status=SubscriberStatus.PENDING,
            token_seed="a-stale-seed-value-0002",
            notice_sent_at=datetime.now(UTC)
            - timedelta(hours=settings.subscription_confirm_ttl_hours + 1),
        )
        db_session.add(stale)
        db_session.commit()
        token = issue_token(TokenPurpose.CONFIRM, stale.token_seed, settings.token_secret)

        response = client.post(CONFIRM, json={"token": token})

        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert response.get_json()["error"]["code"] == "invalid_token"
        assert subscribers(db_session)[0].status is SubscriberStatus.PENDING

    def test_a_confirmation_token_cannot_unsubscribe(
        self, client: FlaskClient, settings: Settings, known: Subscriber
    ) -> None:
        token = token_for(known, TokenPurpose.CONFIRM, settings)

        assert client.post(UNSUBSCRIBE, json={"token": token}).status_code == HTTPStatus.BAD_REQUEST


class TestUnsubscribe:
    """Ending a subscription needs the token and nothing else."""

    def test_the_link_from_a_message_ends_the_subscription_immediately(
        self, client: FlaskClient, settings: Settings, known: Subscriber, db_session: Session
    ) -> None:
        token = token_for(known, TokenPurpose.UNSUBSCRIBE, settings)
        assert unsubscribe_url(known.token_seed, settings).endswith(token)

        response = client.post(UNSUBSCRIBE, json={"token": token})

        assert response.status_code == HTTPStatus.OK
        row = subscribers(db_session)[0]
        assert row.status is SubscriberStatus.UNSUBSCRIBED
        assert row.unsubscribed_at is not None

    def test_unsubscribing_twice_still_reports_success(
        self, client: FlaskClient, settings: Settings, known: Subscriber
    ) -> None:
        token = token_for(known, TokenPurpose.UNSUBSCRIBE, settings)

        first = client.post(UNSUBSCRIBE, json={"token": token})
        second = client.post(UNSUBSCRIBE, json={"token": token})

        assert first.get_json() == second.get_json()

    def test_a_forged_token_is_refused(self, client: FlaskClient, known: Subscriber) -> None:
        forged = issue_token(TokenPurpose.UNSUBSCRIBE, known.token_seed, b"the-wrong-key")

        response = client.post(UNSUBSCRIBE, json={"token": forged})

        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert response.get_json()["error"]["code"] == "invalid_token"

    def test_the_error_never_echoes_the_token(self, client: FlaskClient) -> None:
        # A token is a credential, and an error message is one of the places a credential
        # ends up being logged.
        response = client.post(UNSUBSCRIBE, json={"token": "aaaaaaaaaaaa.bbbbbbbbbbbb"})

        assert "aaaaaaaaaaaa" not in response.get_data(as_text=True)

    def test_resubscribing_retires_the_previous_links(
        self,
        client: FlaskClient,
        settings: Settings,
        known: Subscriber,
        db_session: Session,
        outbox: RecordingMailer,
    ) -> None:
        del outbox
        old_seed = known.token_seed
        old_token = token_for(known, TokenPurpose.UNSUBSCRIBE, settings)
        client.post(UNSUBSCRIBE, json={"token": old_token})

        client.post(SUBSCRIBE, json={"email": "known@example.org"})

        row = subscribers(db_session)[0]
        assert row.status is SubscriberStatus.PENDING
        assert row.token_seed != old_seed

        # The old link is inert: it addressed the subscription that ended, and cannot reach
        # the new one. It still reports success, because the address it was issued for is in
        # fact not on the list — telling the reader otherwise would send them looking for a
        # way out that does not exist.
        stale = client.post(UNSUBSCRIBE, json={"token": old_token})
        assert stale.status_code == HTTPStatus.OK
        assert subscribers(db_session)[0].status is SubscriberStatus.PENDING


class TestValidation:
    """What the endpoints refuse before anything is stored or sent."""

    @pytest.mark.parametrize(
        "address",
        [
            "not-an-address",
            "missing@tld",
            "@example.org",
            "reader@",
            "reader@@example.org",
            "reader@exam ple.org",
            "reader@example.org\nBcc: victim@example.org",
            "reader@example.org\r\nSubject: forged",
            "\x00reader@example.org",
            "a" * 65 + "@example.org",
            "a" * 250 + "@example.org",
            "<script>@example.org",
        ],
    )
    def test_a_malformed_address_is_rejected(
        self, client: FlaskClient, outbox: RecordingMailer, address: str
    ) -> None:
        response = client.post(SUBSCRIBE, json={"email": address})

        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert response.get_json()["error"]["code"] == "validation_error"
        assert outbox.sent == []

    @pytest.mark.parametrize("body", [None, [], "reader@example.org", {}, {"email": 42}])
    def test_a_body_that_is_not_a_request_is_rejected(
        self, client: FlaskClient, outbox: RecordingMailer, body: object
    ) -> None:
        response = client.post(SUBSCRIBE, json=body)

        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert outbox.sent == []

    def test_a_missing_or_oversized_token_is_rejected(self, client: FlaskClient) -> None:
        assert client.post(CONFIRM, json={}).status_code == HTTPStatus.BAD_REQUEST
        assert client.post(CONFIRM, json={"token": "x" * 500}).status_code == (
            HTTPStatus.BAD_REQUEST
        )


class TestAbuse:
    """What stops the form being pointed at somebody else's mailbox."""

    def test_repeated_submissions_send_one_message_per_interval(
        self, client: FlaskClient, outbox: RecordingMailer
    ) -> None:
        for _ in range(5):
            response = client.post(SUBSCRIBE, json={"email": "victim@example.org"})
            assert response.status_code == HTTPStatus.ACCEPTED

        assert len(outbox.sent) == 1

    def test_the_route_is_rate_limited_per_client(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The limit is configuration; this application carries a tiny one so the test does not
        # depend on the shipped default.
        limited_settings = build_settings(
            database_url=f"sqlite+pysqlite:///{tmp_path / 'limited.db'}",
            rate_limit_enabled=True,
            rate_limit_subscribe="2 per hour",
            mail_outbox_dir=tmp_path / "outbox",
        )
        engine = make_engine(limited_settings)
        Base.metadata.create_all(engine)
        engine.dispose()
        monkeypatch.setattr(
            "plt.api.subscriptions.build_mailer", lambda _settings: RecordingMailer()
        )
        application = create_app(limited_settings)
        limiter.reset()
        try:
            with application.test_client() as limited:
                statuses = [
                    limited.post(SUBSCRIBE, json={"email": f"r{index}@example.org"}).status_code
                    for index in range(4)
                ]
        finally:
            limiter.reset()
            dispose_database(application)

        assert statuses == [
            HTTPStatus.ACCEPTED,
            HTTPStatus.ACCEPTED,
            HTTPStatus.TOO_MANY_REQUESTS,
            HTTPStatus.TOO_MANY_REQUESTS,
        ]

    def test_a_mail_failure_neither_stores_nor_leaks(
        self, client: FlaskClient, monkeypatch: pytest.MonkeyPatch, db_session: Session
    ) -> None:
        monkeypatch.setattr(
            "plt.api.subscriptions.build_mailer", lambda _settings: RecordingMailer(fail=True)
        )

        response = client.post(SUBSCRIBE, json={"email": "reader@example.org"})

        assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        assert response.get_json()["error"]["code"] == "mail_unavailable"
        body = response.get_data(as_text=True).lower()
        assert "smtp" not in body
        assert "traceback" not in body
        # Nothing was recorded, so the next attempt sends rather than being throttled out by
        # a failure the reader never saw.
        assert subscribers(db_session) == []
