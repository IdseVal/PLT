"""Telling an administrator that the review queue has something in it.

``case_review`` records a borderline case; nothing until now told anybody it existed. A queue
that has to be polled by someone who remembers to poll it is, in practice, a queue nobody
reads, and core document section 2.7 leans on review to buy back the precision that the
recall-first threshold gives away. The flag has to reach a person (or an agent) without being
made public.

**Not public, and not a mailing list.** Flags stay off every public endpoint — ``/api/reviews``
still needs its bearer token, and a rejection still shows as a plain 404 on ``/api/cases``.
This notification goes to one address from configuration, ``PLT_ADMIN_EMAIL``, and carries no
unsubscribe link: it is operational mail to the operator, not a subscription. Unset means no
notification is sent, and the queue is unaffected either way.

**One message per run, not one per case.** A scan that flags forty cases sends one message
listing them. An alarm that fires forty times is one nobody reads, which is the failure mode
core document section 2.11 describes for quarantine and is just as true here.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from plt.config import Settings
from plt.db.repositories import count_reviews_flagged_since, reviews_flagged_since
from plt.notifications.mailer import Mailer
from plt.notifications.messages import review_notice_message
from plt.utils.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = ["notify_new_reviews"]

log = get_logger(__name__)


def notify_new_reviews(
    session: Session,
    settings: Settings,
    mailer: Mailer,
    *,
    since: datetime,
) -> int:
    """Email the administrator the review items flagged since an instant.

    Args:
        session: Open database session.
        settings: Validated settings supplying the administrator's address and the cap on how
            many items one message lists.
        mailer: Backend to send through.
        since: Inclusive lower bound on ``case_review.flagged_at``. The ingestion CLI passes
            the moment its runs started, so a notification covers exactly that scan.

    Returns:
        The number of items the notification covered; ``0`` when there was nothing to report
        or no administrator is configured, in which case nothing is sent.

    Raises:
        MailError: If the message could not be delivered. The caller decides whether that is
            fatal; for the ingestion CLI it is not, because the scan itself succeeded.
    """
    recipient = (settings.admin_email or "").strip()
    if not recipient:
        log.debug("no PLT_ADMIN_EMAIL is configured; the review queue notice is not sent")
        return 0

    total = count_reviews_flagged_since(session, since=since)
    if total == 0:
        return 0

    items = reviews_flagged_since(session, since=since, limit=settings.admin_notice_max_items)
    mailer.send(review_notice_message(recipient, items, settings, since=since, total=total))
    log.info(
        "review queue notice sent",
        extra={"context": {"items": total, "listed": len(items), "since": since.isoformat()}},
    )
    return total
