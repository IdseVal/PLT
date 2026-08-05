"""Mailing-list endpoints: ``/api/subscriptions`` and below.

Four public, unauthenticated routes, and every one of them is written around the same two
constraints.

**Nothing here may reveal who is on the list.** A subscriber's address is personal data, and
an endpoint that answered differently for a known address than for an unknown one would let
anyone with a word list test addresses against the tracker. So:

* ``POST /api/subscriptions`` and ``POST /api/subscriptions/unsubscribe-link`` answer with one
  fixed body and one fixed status, whatever the state of the address behind it — unknown,
  pending, confirmed, previously unsubscribed, or throttled out of a message entirely.
* No route lists subscribers, counts them, or takes an address as a path or query parameter
  where it would reach a log or a referrer header. Addresses travel in a JSON body, over
  ``POST``, and nowhere else.
* ``confirm`` and ``unsubscribe`` do report success or failure, because the caller is holding
  a token that only the address itself could have received, and a page that could not say
  whether it had worked would be unusable. What they report is about *the token*, never about
  an address: a forged token, an expired one and one for a subscription that has since gone
  are answered identically.

**They send mail, so they are the abuse surface.** An unauthenticated endpoint that sends
email to an address a stranger supplies is the classic way to have somebody else's server
bombard a third party. Three things bound it: a strict per-client rate limit from
configuration, a per-address interval below which no second message is sent however many
requests arrive, and validation that refuses anything that is not an ordinary address.

**State changes are ``POST``.** The unsubscribe link in an email points at a *page*, which
posts the token. A mailbox provider's link scanner following the URL therefore cannot
unsubscribe anybody by looking at it, while ``List-Unsubscribe-Post`` still lets a mail client
do it in one click.

**No CSRF token, and none is needed.** These routes carry no ambient authority: there is no
session, no cookie and no credential a browser could attach on a caller's behalf, so a
cross-site request achieves nothing an attacker could not achieve by calling the endpoint
directly. Each also requires a JSON body, which a cross-origin ``fetch`` can only send after a
preflight the configured CORS policy refuses, and which an HTML form cannot send at all.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from flask import Blueprint, request

from plt.api.errors import ApiError
from plt.api.schemas import parse_subscription_request, parse_subscription_token
from plt.extensions import current_settings, db_session, limiter
from plt.notifications.mailer import MailError, build_mailer
from plt.notifications.subscriptions import (
    confirm_subscription,
    request_subscription,
    request_unsubscribe_link,
    unsubscribe,
)
from plt.utils.logging import get_logger

__all__ = ["subscriptions_bp"]

log = get_logger(__name__)

#: Mounted at ``<api_prefix>/subscriptions`` by :func:`plt.api.register_blueprints`.
subscriptions_bp = Blueprint("subscriptions", __name__)

#: The one answer both mail-sending routes give. It describes what the *caller* should do and
#: says nothing about what the server found, which is what keeps it from being an oracle. It
#: also states the double opt-in accurately rather than reassuringly: a submitted address is
#: recorded so the confirmation can be checked, and it is simply never sent a digest until
#: the link is used.
_ACCEPTED = (
    "If that address needs an email from us, one is on its way. Open the link in it to "
    "finish: no digest is ever sent to an address that has not confirmed."
)


class InvalidTokenError(ApiError):
    """The link's token was missing, malformed, expired or not one this deployment issued."""

    status_code = HTTPStatus.BAD_REQUEST
    code = "invalid_token"


class MailUnavailableError(ApiError):
    """The message could not be handed to the mail backend."""

    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "mail_unavailable"


def _subscribe_rate_limit() -> str:
    """Return the configured rate limit for the routes that send mail.

    Evaluated per request rather than at import time, so the limit stays configuration and a
    test application can carry a different one.

    Returns:
        A limit expression such as ``5 per hour``.
    """
    return current_settings().rate_limit_subscribe


def _token_rate_limit() -> str:
    """Return the configured rate limit for the confirm and unsubscribe routes.

    Returns:
        A limit expression such as ``30 per hour``.
    """
    return current_settings().rate_limit_subscription_token


@subscriptions_bp.post("")
@limiter.limit(_subscribe_rate_limit)
def subscribe() -> tuple[dict[str, Any], int]:
    """Take an address and send it a confirmation link.

    Body: ``{"email": "..."}``. The address is recorded as pending and is sent exactly one
    message. It receives nothing else, and no digest, until the link in that message is used:
    without that step the form would let anyone put anyone else's address on the list.

    Returns:
        A ``(payload, status)`` pair. Always ``202`` with the same body, whatever the state of
        the address — see the module docstring.

    Raises:
        MailUnavailableError: If the backend could not accept the message. The row stays as it
            was, so a later attempt sends rather than being throttled out by the failure.
    """
    settings = current_settings()
    email = parse_subscription_request(request.get_json(silent=True))
    session = db_session()

    mailer = build_mailer(settings)
    try:
        action = request_subscription(session, settings, mailer, email)
    except MailError:
        session.rollback()
        log.exception("a subscription confirmation could not be sent")
        message = "The confirmation email could not be sent. Please try again later."
        raise MailUnavailableError(message) from None
    finally:
        mailer.close()

    session.commit()
    # The address is not logged: it is personal data, and rule 2.7 keeps it out of the logs.
    log.info("subscription request handled", extra={"context": {"action": str(action.value)}})
    return {"status": "accepted", "message": _ACCEPTED}, HTTPStatus.ACCEPTED


@subscriptions_bp.post("/unsubscribe-link")
@limiter.limit(_subscribe_rate_limit)
def unsubscribe_link() -> tuple[dict[str, Any], int]:
    """Send an address its own unsubscribe link, for somebody who has no email to hand.

    Body: ``{"email": "..."}``. This is what makes the unsubscribe flow reachable from the
    website rather than only from a message. The link goes to the address itself, so nobody
    can cancel somebody else's subscription, and an address that is not on the list is sent
    nothing at all — mailing an unknown address on an anonymous request would be exactly the
    unsolicited mail this module exists to prevent.

    Returns:
        A ``(payload, status)`` pair. Always ``202`` with the same body as
        :func:`subscribe`.

    Raises:
        MailUnavailableError: If the backend could not accept the message.
    """
    settings = current_settings()
    email = parse_subscription_request(request.get_json(silent=True))
    session = db_session()

    mailer = build_mailer(settings)
    try:
        action = request_unsubscribe_link(session, settings, mailer, email)
    except MailError:
        session.rollback()
        log.exception("an unsubscribe link could not be sent")
        message = "The email could not be sent. Please try again later."
        raise MailUnavailableError(message) from None
    finally:
        mailer.close()

    session.commit()
    log.info("unsubscribe link requested", extra={"context": {"action": str(action.value)}})
    return {"status": "accepted", "message": _ACCEPTED}, HTTPStatus.ACCEPTED


@subscriptions_bp.post("/confirm")
@limiter.limit(_token_rate_limit)
def confirm() -> tuple[dict[str, Any], int]:
    """Complete a double opt-in.

    Body: ``{"token": "..."}``, taken from the link in the confirmation email. Idempotent: a
    reader who opens the link twice is confirmed once and told it worked both times.

    Returns:
        A ``(payload, status)`` pair confirming the subscription.

    Raises:
        InvalidTokenError: If the token does not verify, has expired, or belongs to a
            subscription that has since been cancelled. The three are not distinguished.
    """
    settings = current_settings()
    token = parse_subscription_token(request.get_json(silent=True))
    session = db_session()

    if not confirm_subscription(session, settings, token):
        message = (
            "This confirmation link is not valid. It may have expired, or already been "
            "replaced by a newer one. Subscribing again sends a fresh link."
        )
        raise InvalidTokenError(message)

    session.commit()
    return {
        "status": "confirmed",
        "message": "Your address is confirmed. The next weekly digest will include you.",
    }, HTTPStatus.OK


@subscriptions_bp.post("/unsubscribe")
@limiter.limit(_token_rate_limit)
def cancel() -> tuple[dict[str, Any], int]:
    """End a subscription, immediately, with no authentication but the token.

    Body: ``{"token": "..."}``, taken from the link in any message the list sent. Idempotent,
    and generous: a token that verifies but whose subscription has already gone reports
    success, because the address is in fact not on the list.

    Returns:
        A ``(payload, status)`` pair confirming that the address is off the list.

    Raises:
        InvalidTokenError: If the token does not verify.
    """
    settings = current_settings()
    token = parse_subscription_token(request.get_json(silent=True))
    session = db_session()

    if not unsubscribe(session, settings, token):
        message = (
            "This unsubscribe link is not valid. Ask for a new one from the unsubscribe page "
            "and it will be sent to your address."
        )
        raise InvalidTokenError(message)

    session.commit()
    return {
        "status": "unsubscribed",
        "message": "Your address has been removed from the mailing list.",
    }, HTTPStatus.OK
