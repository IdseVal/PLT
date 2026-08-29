"""The messages the tracker sends, as plain text.

Every message is built here, so what a subscriber or an administrator receives can be read
in one file rather than assembled across the code base. Four rules run through all of them:

* **Plain text only.** No HTML alternative, therefore no tracking pixel, and no link is
  rewritten through a redirector. Nothing about a reader's behaviour is observable, because
  nothing is there to observe.
* **Every subscriber message carries its unsubscribe link**, in the body and in the
  ``List-Unsubscribe`` header, so the way out never depends on finding an old email.
* **One recipient per message.** A digest addressed to several people would disclose the
  mailing list to everyone on it.
* **Say what is stored.** Each message states, in a sentence, what the tracker holds and how
  to end it. That is the transparency the GDPR asks for, and it costs four lines.

Case titles come from court sources and reach a mail client unchanged: plain text is not a
markup context, so there is nothing to escape, but the text is still bounded in length so one
pathological title cannot dominate a digest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final
from urllib.parse import quote

from plt.config import Settings
from plt.notifications.mailer import Message
from plt.notifications.tokens import TokenPurpose, issue_token

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from plt.db.models import Case, CaseReview

__all__ = [
    "confirm_url",
    "confirmation_message",
    "digest_message",
    "review_notice_message",
    "unsubscribe_link_message",
    "unsubscribe_url",
]

#: Longest title reproduced in a listing. A court title is occasionally a paragraph.
_MAX_TITLE = 160

#: Longest filter reason reproduced in the admin notification.
_MAX_REASON = 300

#: Separator between entries in a listing.
_RULE: Final[str] = "-" * 68


def _truncate(value: str | None, limit: int) -> str:
    """Shorten a value from an external source for a listing.

    Args:
        value: The text, or ``None``.
        limit: Longest result, including the ellipsis.

    Returns:
        The text on one line, shortened with an ellipsis when it was too long, or an empty
        string when there was none.
    """
    if not value:
        return ""
    collapsed = " ".join(value.split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[: limit - 1]}…"


def confirm_url(seed: str, settings: Settings) -> str:
    """Build the link that confirms a subscription.

    Args:
        seed: The subscriber's stored selector.
        settings: Settings supplying the site origin and the token key.

    Returns:
        An absolute URL into the site's confirmation page.
    """
    token = issue_token(TokenPurpose.CONFIRM, seed, settings.token_secret)
    return settings.site_url(f"/subscribe/confirm?token={quote(token, safe='')}")


def unsubscribe_url(seed: str, settings: Settings) -> str:
    """Build the link that ends a subscription.

    The same URL appears in the body and in the ``List-Unsubscribe`` header of every message
    the list sends, and it needs no login: the token in it is the authorisation.

    Args:
        seed: The subscriber's stored selector.
        settings: Settings supplying the site origin and the token key.

    Returns:
        An absolute URL into the site's unsubscribe page.
    """
    token = issue_token(TokenPurpose.UNSUBSCRIBE, seed, settings.token_secret)
    return settings.site_url(f"/unsubscribe?token={quote(token, safe='')}")


def _case_line(case: Case, settings: Settings) -> str:
    """Render one case as a listing entry.

    Args:
        case: The case to render.
        settings: Settings supplying the site origin.

    Returns:
        Two or three lines of text: what it is, and where to read it.
    """
    title = _truncate(case.title, _MAX_TITLE) or case.source_id
    where = case.jurisdiction_code
    if case.court is not None and case.court.name:
        where = f"{where} · {_truncate(case.court.name, 80)}"
    if case.decision_date is not None:
        where = f"{where} · {case.decision_date.isoformat()}"
    path = f"/cases/{quote(case.jurisdiction_code, safe='')}/{quote(case.source_id, safe='')}"
    return f"{title}\n  {where}\n  {settings.site_url(path)}"


def _list_headers(seed: str, settings: Settings) -> dict[str, str]:
    """Return the list-management headers every subscriber message carries.

    ``List-Unsubscribe`` is what puts an unsubscribe button in the mail client itself, and
    ``List-Unsubscribe-Post`` (RFC 8058) tells the client it may act on it without the reader
    leaving their inbox. The endpoint behind it changes state on ``POST`` only, so a link
    scanner that follows the URL cannot unsubscribe anyone by looking at it.

    Args:
        seed: The subscriber's stored selector.
        settings: Settings supplying the site origin and the token key.

    Returns:
        The headers to attach.
    """
    return {
        "List-Unsubscribe": f"<{unsubscribe_url(seed, settings)}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def _footer(seed: str, settings: Settings) -> str:
    """Return the standing footer of a subscriber message.

    Args:
        seed: The subscriber's stored selector.
        settings: Settings supplying the site origin and the token key.

    Returns:
        The unsubscribe link and the sentence about what is stored.
    """
    return (
        f"To stop receiving these emails, open this link. It works immediately and\n"
        f"needs no password:\n"
        f"  {unsubscribe_url(seed, settings)}\n"
        f"\n"
        f"The tracker stores your email address, whether it is confirmed, and the dates\n"
        f"those things happened. Nothing else: no name, and no record of whether you\n"
        f"open this message or follow a link in it. Your address is used to send this\n"
        f"alert and for no other purpose, and it is never passed on.\n"
        f"  {settings.site_url('/')}"
    )


def confirmation_message(email: str, seed: str, settings: Settings) -> Message:
    """Build the double opt-in confirmation request.

    Until this link is used, the address receives nothing else — which is the point: without
    a confirmation step, anybody could put anybody else's address on the list.

    Args:
        email: The address that was submitted.
        seed: The subscriber's stored selector.
        settings: Settings supplying the site origin and the token key.

    Returns:
        The message to send.
    """
    body = (
        f"Somebody — probably you — asked for email alerts from the Pesticide\n"
        f"Litigation Tracker at {settings.site_base_url}.\n"
        f"\n"
        f"Confirm the address by opening this link:\n"
        f"  {confirm_url(seed, settings)}\n"
        f"\n"
        f"The link is valid for {settings.subscription_confirm_ttl_hours} hours. Until it is "
        f"used, this address is on no\n"
        f"list and will receive no further email from us.\n"
        f"\n"
        f"If this was not you, do nothing. Nothing has been subscribed, and the\n"
        f"request expires by itself.\n"
        f"\n"
        f"— {settings.app_name}, Wageningen Law\n"
    )
    return Message(
        to=email,
        subject="Confirm your Pesticide Litigation Tracker alerts",
        body=body,
    )


def unsubscribe_link_message(email: str, seed: str, settings: Settings) -> Message:
    """Build the message that carries an unsubscribe link on request.

    Sent when an address that is already on the list is submitted again, and when somebody
    asks for their unsubscribe link from the site without an email to hand. Both cases are
    the same message, because both are answered the same way: the link goes to the address
    itself, so nobody can cancel anybody else's subscription and nobody learns whether an
    address is on the list.

    Args:
        email: The address concerned.
        seed: The subscriber's stored selector.
        settings: Settings supplying the site origin and the token key.

    Returns:
        The message to send.
    """
    body = (
        f"This address is already receiving alerts from the Pesticide Litigation\n"
        f"Tracker, so nothing has changed.\n"
        f"\n"
        f"{_footer(seed, settings)}\n"
    )
    return Message(
        to=email,
        subject="Your Pesticide Litigation Tracker subscription",
        body=body,
        headers=_list_headers(seed, settings),
    )


def digest_message(
    email: str,
    seed: str,
    cases: Sequence[Case],
    settings: Settings,
    *,
    since: datetime,
    until: datetime,
    total: int,
) -> Message:
    """Build one subscriber's digest of newly found cases.

    Args:
        email: The recipient.
        seed: The subscriber's stored selector, for the unsubscribe link.
        cases: The cases to list, already bounded by ``digest_max_cases``.
        settings: Settings supplying the site origin and the token key.
        since: Start of the window the cases were found in.
        until: End of that window.
        total: How many cases the window held in all, which may exceed ``len(cases)``.

    Returns:
        The message to send.
    """
    window = f"{since.date().isoformat()} to {until.date().isoformat()}"
    listing = f"\n\n{_RULE}\n\n".join(_case_line(case, settings) for case in cases)
    more = ""
    if total > len(cases):
        more = (
            f"\n\n{len(cases)} of {total} new cases are listed above. The rest are in the\n"
            f"collection:\n  {settings.site_url('/cases')}"
        )
    plural = "case" if total == 1 else "cases"
    body = (
        f"{total} new {plural} entered the Pesticide Litigation Tracker between\n"
        f"{window}.\n"
        f"\n"
        f"{_RULE}\n"
        f"\n"
        f"{listing}"
        f"{more}\n"
        f"\n"
        f"{_RULE}\n"
        f"\n"
        f"How cases are selected, and what the tracker does not contain, is described\n"
        f"on the methodology page:\n"
        f"  {settings.site_url('/methodology')}\n"
        f"\n"
        f"{_footer(seed, settings)}\n"
    )
    return Message(
        to=email,
        subject=f"Pesticide Litigation Tracker: {total} new {plural}",
        body=body,
        headers=_list_headers(seed, settings),
    )


def _review_line(review: CaseReview, settings: Settings) -> str:
    """Render one review-queue item for the administrator's notification.

    Args:
        review: The queued item, with its case loaded.
        settings: Settings supplying the site origin.

    Returns:
        The identifier, the numbers the flag came from, and where to read the case.
    """
    case = review.case
    title = _truncate(case.title, _MAX_TITLE) or case.source_id
    count = case.matched_term_count
    band = (
        "matched terms unknown"
        if count is None
        else f"{count} matched term{'' if count == 1 else 's'}"
    )
    path = f"/cases/{quote(case.jurisdiction_code, safe='')}/{quote(case.source_id, safe='')}"
    return (
        f"{case.jurisdiction_code} {case.source_id}\n"
        f"  {title}\n"
        f"  {band}; list {review.list_version or 'unknown'}\n"
        f"  {_truncate(review.reason, _MAX_REASON)}\n"
        f"  {settings.site_url(path)}"
    )


def review_notice_message(
    to: str,
    reviews: Sequence[CaseReview],
    settings: Settings,
    *,
    since: datetime,
    total: int,
) -> Message:
    """Build the administrator's notification of new review-queue items.

    The queue is not public — ``/api/reviews`` needs a bearer token and lists cases a
    rejection has unpublished — so this message is what makes a flag visible at all
    (core document section 2.7). It goes to one configured address and carries no
    unsubscribe link: it is operational mail to an administrator, not a mailing list.

    Args:
        to: The administrator's address, from configuration.
        reviews: The flagged items to list, already bounded by ``admin_notice_max_items``.
        settings: Settings supplying the site origin.
        since: Start of the window the flags were raised in.
        total: How many items were flagged in all.

    Returns:
        The message to send.
    """
    listing = f"\n\n{_RULE}\n\n".join(_review_line(review, settings) for review in reviews)
    more = ""
    if total > len(reviews):
        more = f"\n\n{len(reviews)} of {total} items are listed above."
    plural = "case" if total == 1 else "cases"
    body = (
        f"{total} {plural} entered the review queue since {since.isoformat()}.\n"
        f"\n"
        f"Each scored inside its keyword list's review band: published like any other\n"
        f"case, and additionally queued for a content manager to confirm or reject\n"
        f"(core document 2.7). A rejection unpublishes the case and deletes nothing.\n"
        f"\n"
        f"{_RULE}\n"
        f"\n"
        f"{listing}"
        f"{more}\n"
        f"\n"
        f"{_RULE}\n"
        f"\n"
        f"The queue itself is at GET /api/reviews on the deployment, which needs the\n"
        f"review bearer token. This message is sent to PLT_ADMIN_EMAIL and to nobody\n"
        f"else; the flags stay out of the public API.\n"
    )
    return Message(
        to=to,
        subject=f"PLT review queue: {total} new {plural} to check",
        body=body,
    )
