"""The subscription lifecycle: subscribe, confirm, unsubscribe.

The rules the mailing list is built on, in one module so they can be read together.

**Double opt-in.** A submitted address is stored as ``pending`` and receives exactly one
message: its own confirmation request. Only :func:`confirm_subscription` moves it to
``confirmed``, and only a ``confirmed`` address is ever sent a digest. Without that step the
front-page form would let anyone subscribe anyone.

**No enumeration.** Every branch of :func:`request_subscription` ends the same way from
outside: the endpoint answers identically whether the address was unknown, pending, confirmed
or previously unsubscribed. The throttle is part of that, not an exception to it — it is
keyed on a row that *every* branch creates or updates, so "no message was sent because one
went recently" cannot be reached for one class of address and not another.

**Unsubscribe needs no account.** The token in the link is the authorisation. It is verified
before the database is touched, it is bound to the unsubscribe purpose so a confirmation link
cannot be replayed as one, and the effect is immediate.

**Anti-abuse.** The subscribe endpoint is unauthenticated, public and sends mail, which is
the classic vector for using somebody else's server to bombard a third party.
``subscription_notice_interval_seconds`` bounds it: the second submission for an address
inside the window sends nothing at all, so the ceiling on messages to any one address is one
per interval however many times the form is submitted, from however many hosts. The per-client
rate limit on the route bounds the other axis.

Validation is not here: an address arrives already parsed and normalised by
``plt.api.schemas``, which is where request validation lives.
"""

from __future__ import annotations

import enum
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from plt.config import Settings
from plt.db.base import utcnow
from plt.db.models import Subscriber, SubscriberStatus
from plt.db.repositories import get_subscriber_by_email, get_subscriber_by_token_seed
from plt.notifications.mailer import Mailer, Message
from plt.notifications.messages import confirmation_message, unsubscribe_link_message
from plt.notifications.tokens import TokenPurpose, new_seed, verify_token
from plt.utils.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = [
    "SubscriptionAction",
    "confirm_subscription",
    "request_subscription",
    "request_unsubscribe_link",
    "unsubscribe",
]

log = get_logger(__name__)


class SubscriptionAction(enum.StrEnum):
    """What a subscription request actually did.

    Returned for logging and for tests. **It is never sent to the client**: the HTTP response
    is the same sentence whichever of these happened, because the difference between them is
    precisely what an address-checking oracle would report.
    """

    #: A new address was recorded and sent a confirmation request.
    CREATED = "created"
    #: A pending address was sent its confirmation request again.
    RESENT = "resent"
    #: A previously unsubscribed address started a fresh subscription.
    RESUBSCRIBED = "resubscribed"
    #: An address already on the list was reminded, with its unsubscribe link.
    REMINDED = "reminded"
    #: Nothing was sent: this address had a message too recently.
    THROTTLED = "throttled"
    #: Nothing was sent, and nothing was recorded.
    IGNORED = "ignored"


def _throttled(subscriber: Subscriber, settings: Settings, now: datetime) -> bool:
    """Return whether this address has had a message too recently to have another.

    Args:
        subscriber: The row concerned.
        settings: Settings supplying the interval.
        now: The current instant.

    Returns:
        ``True`` when the last transactional message is inside the configured interval.
    """
    if subscriber.notice_sent_at is None:
        return False
    interval = timedelta(seconds=settings.subscription_notice_interval_seconds)
    return now - subscriber.notice_sent_at < interval


def _deliver(mailer: Mailer, message: Message, subscriber: Subscriber, now: datetime) -> None:
    """Send one transactional message and record that this address was written to.

    The timestamp is written whatever the message was, which is what keeps the throttle from
    telling a known address from an unknown one.

    Args:
        mailer: The backend to send through.
        message: The message to send.
        subscriber: The row to stamp.
        now: The current instant.

    Raises:
        MailError: If the message could not be delivered. The timestamp is then not written,
            so a later attempt may retry rather than being throttled out by a failure.
    """
    mailer.send(message)
    subscriber.notice_sent_at = now


def request_subscription(
    session: Session,
    settings: Settings,
    mailer: Mailer,
    email: str,
    *,
    now: datetime | None = None,
) -> SubscriptionAction:
    """Handle a submission of the front-page signup form.

    Args:
        session: Open database session. The caller commits.
        settings: Validated settings.
        mailer: Backend to send the confirmation through.
        email: The address, already validated and normalised by the API layer.
        now: The current instant, for tests.

    Returns:
        What was done, for the log. The caller must not tell the client.

    Raises:
        MailError: If the message could not be sent.
    """
    moment = now if now is not None else utcnow()
    subscriber = get_subscriber_by_email(session, email)

    if subscriber is None:
        subscriber = Subscriber(
            email=email,
            status=SubscriberStatus.PENDING,
            token_seed=new_seed(),
            created_at=moment,
        )
        session.add(subscriber)
        session.flush()
        _deliver(
            mailer, confirmation_message(email, subscriber.token_seed, settings), subscriber, moment
        )
        return SubscriptionAction.CREATED

    if _throttled(subscriber, settings, moment):
        # Deliberately silent. This is the ceiling on how often one address can be written
        # to, and therefore what stops the form being used to bombard somebody.
        return SubscriptionAction.THROTTLED

    if subscriber.status is SubscriberStatus.CONFIRMED:
        # Telling the *address* that it is already subscribed is safe and useful; telling the
        # submitter would not be. The message carries the unsubscribe link, so an address that
        # somebody else keeps submitting has a way out in front of it.
        _deliver(
            mailer,
            unsubscribe_link_message(email, subscriber.token_seed, settings),
            subscriber,
            moment,
        )
        return SubscriptionAction.REMINDED

    action = SubscriptionAction.RESENT
    if subscriber.status is SubscriberStatus.UNSUBSCRIBED:
        # A fresh subscription, so a fresh seed: the links of the previous one stop working.
        subscriber.status = SubscriberStatus.PENDING
        subscriber.token_seed = new_seed()
        subscriber.confirmed_at = None
        subscriber.unsubscribed_at = None
        action = SubscriptionAction.RESUBSCRIBED
    _deliver(
        mailer, confirmation_message(email, subscriber.token_seed, settings), subscriber, moment
    )
    return action


def request_unsubscribe_link(
    session: Session,
    settings: Settings,
    mailer: Mailer,
    email: str,
    *,
    now: datetime | None = None,
) -> SubscriptionAction:
    """Send an address its own unsubscribe link, for the flow that starts on the website.

    Reaching the unsubscribe page without an email in hand still has to work, and the only way
    to do that without either a login or an open invitation to cancel other people's
    subscriptions is to send the link to the address itself.

    An address that is not on the list is sent **nothing**: mailing an unknown address on an
    anonymous request would be exactly the unsolicited mail this module exists to prevent. The
    response the caller returns is identical either way, so no client can tell the difference
    from what it is told. A determined observer could time the two branches apart on a live
    SMTP server; that is accepted here, bounded by the per-client rate limit, and it is a far
    smaller exposure than mailing every address somebody cares to submit.

    Args:
        session: Open database session. The caller commits.
        settings: Validated settings.
        mailer: Backend to send through.
        email: The address, already validated and normalised.
        now: The current instant, for tests.

    Returns:
        What was done, for the log.

    Raises:
        MailError: If the message could not be sent.
    """
    moment = now if now is not None else utcnow()
    subscriber = get_subscriber_by_email(session, email)
    if subscriber is None or subscriber.status is SubscriberStatus.UNSUBSCRIBED:
        return SubscriptionAction.IGNORED
    if _throttled(subscriber, settings, moment):
        return SubscriptionAction.THROTTLED
    _deliver(
        mailer,
        unsubscribe_link_message(email, subscriber.token_seed, settings),
        subscriber,
        moment,
    )
    return SubscriptionAction.REMINDED


def confirm_subscription(
    session: Session,
    settings: Settings,
    token: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Complete a double opt-in.

    Idempotent: a subscriber who opens the link twice is confirmed once and told it worked
    both times. An expired link, a link whose subscription has since been cancelled, and a
    forged token are all reported the same way, so the outcome never describes an address.

    Args:
        session: Open database session. The caller commits.
        settings: Validated settings supplying the token key and the time to live.
        token: The token from the link.
        now: The current instant, for tests.

    Returns:
        ``True`` when the address is confirmed as a result, ``False`` otherwise.
    """
    moment = now if now is not None else utcnow()
    seed = verify_token(TokenPurpose.CONFIRM, token, settings.token_secret)
    if seed is None:
        log.info("subscription confirmation presented an invalid token")
        return False

    subscriber = get_subscriber_by_token_seed(session, seed)
    if subscriber is None or subscriber.status is SubscriberStatus.UNSUBSCRIBED:
        return False
    if subscriber.status is SubscriberStatus.CONFIRMED:
        return True

    expiry = timedelta(hours=settings.subscription_confirm_ttl_hours)
    if subscriber.notice_sent_at is None or moment - subscriber.notice_sent_at > expiry:
        log.info(
            "subscription confirmation link had expired",
            extra={"context": {"subscriber_id": subscriber.id}},
        )
        return False

    subscriber.status = SubscriberStatus.CONFIRMED
    subscriber.confirmed_at = moment
    log.info("subscription confirmed", extra={"context": {"subscriber_id": subscriber.id}})
    return True


def unsubscribe(
    session: Session,
    settings: Settings,
    token: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Cancel a subscription, immediately and without any authentication but the token.

    Idempotent, and generous about what counts as success: a token that verifies but whose row
    has already gone reports success, because the address is in fact not on the list and
    telling the reader otherwise would send them looking for a way out that does not exist.
    Only a token that does not verify is a failure.

    Args:
        session: Open database session. The caller commits.
        settings: Validated settings supplying the token key.
        token: The token from the link.
        now: The current instant, for tests.

    Returns:
        ``True`` when the address is off the list as a result, ``False`` when the token was
        not valid.
    """
    moment = now if now is not None else utcnow()
    seed = verify_token(TokenPurpose.UNSUBSCRIBE, token, settings.token_secret)
    if seed is None:
        log.info("unsubscribe presented an invalid token")
        return False

    subscriber = get_subscriber_by_token_seed(session, seed)
    if subscriber is None:
        return True
    if subscriber.status is not SubscriberStatus.UNSUBSCRIBED:
        subscriber.status = SubscriberStatus.UNSUBSCRIBED
        subscriber.unsubscribed_at = moment
        log.info("subscription cancelled", extra={"context": {"subscriber_id": subscriber.id}})
    return True
