"""Storage limitation for the mailing list: what is dropped, when, and by whose decision.

Pseudonymising an address on unsubscribe (core document section 2.12) reduces what the
project holds; it does not end the obligation. A pseudonymised row is still personal data —
the digest recognises a returning address, which is precisely what keeps it in scope — so
GDPR Article 5(1)(e) still applies to it, and so does the separate question of an address
that was submitted and never confirmed, whose owner consented to nothing at all.

**Neither period is decided here.** Both are open questions on issue #75 and belong to the
Law group, so both are settings that default to *unset*, and unset means **not enforced**:

* ``PLT_SUBSCRIBER_RETENTION_DAYS`` — after this long, an unsubscribed row's digest is
  dropped and the row becomes dates and a counter. It is the horizon after which the project
  stops being able to recognise a returning address, so it is also the horizon after which a
  withdrawn address could be subscribed again by anybody who types it in.
* ``PLT_SUBSCRIBER_UNCONFIRMED_EXPIRY_DAYS`` — after this long, an address that never used
  its confirmation link is deleted outright. Nothing is kept, because a row that never
  confirmed records no consent to keep evidence of.

A default here would be a policy nobody chose, dressed as an implementation detail, so there
is none. A deployment that has set neither runs this job and it does nothing, which is the
correct behaviour while the question is open — and the report says so rather than reporting a
silent zero.

The work is batched, committed per batch and interruptible, like every other long-running job
in the project: a purge that dies half way leaves what it committed, and the next run
continues from the same rule rather than from a position it has to remember.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from plt.config import Settings, get_settings
from plt.db.base import utcnow
from plt.db.models import Subscriber, SubscriberStatus
from plt.db.session import get_session_factory, session_scope
from plt.utils.logging import get_logger
from plt.utils.shutdown import StopRequest

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

__all__ = ["PurgeReport", "run_purge"]

log = get_logger(__name__)

#: Rows touched per committed batch. Small enough that an interrupted run loses little and
#: that memory stays flat however long the list gets.
_BATCH_SIZE = 200


@dataclass(slots=True)
class PurgeReport:
    """What one purge did.

    Attributes:
        digests_dropped: Unsubscribed rows whose digest was removed, leaving dates and a
            counter. The rows themselves survive.
        unconfirmed_deleted: Rows deleted because they never confirmed.
        retention_enforced: Whether a retention horizon was configured at all.
        expiry_enforced: Whether an expiry period for unconfirmed addresses was configured.
        interrupted: Whether a signal ended the run before it had finished.
    """

    digests_dropped: int = 0
    unconfirmed_deleted: int = 0
    retention_enforced: bool = False
    expiry_enforced: bool = False
    interrupted: bool = False

    def summary(self) -> str:
        """Return a one-line human summary, for the CLI.

        An unset period is reported as *not configured* rather than as a count of zero: the
        two look identical in a log and mean opposite things — one is a policy applied and
        nothing matched, the other is a policy nobody has decided yet (#75).

        Returns:
            The summary line.
        """
        retention = (
            f"{self.digests_dropped} digest(s) dropped"
            if self.retention_enforced
            else "retention: not configured"
        )
        expiry = (
            f"{self.unconfirmed_deleted} unconfirmed row(s) deleted"
            if self.expiry_enforced
            else "unconfirmed expiry: not configured"
        )
        state = " (interrupted)" if self.interrupted else ""
        return f"{retention}; {expiry}{state}"


def _drop_expired_digests(
    session: Session, *, before: datetime, after_id: int, limit: int
) -> tuple[int, int]:
    """Drop the digest from one batch of unsubscribed rows past the retention horizon.

    Args:
        session: Open session. The caller commits.
        before: Rows unsubscribed strictly before this instant are past the horizon.
        after_id: Exclusive lower bound on the primary key, for keyset pagination.
        limit: Batch size.

    Returns:
        A ``(dropped, cursor)`` pair. ``cursor`` is the last primary key seen, or ``after_id``
        when the batch was empty.
    """
    rows = session.scalars(
        select(Subscriber)
        .where(
            Subscriber.status == SubscriberStatus.UNSUBSCRIBED,
            Subscriber.email_digest.is_not(None),
            Subscriber.unsubscribed_at.is_not(None),
            Subscriber.unsubscribed_at < before,
            Subscriber.id > after_id,
        )
        .order_by(Subscriber.id)
        .limit(limit)
    ).all()
    for row in rows:
        row.email_digest = None
    return len(rows), rows[-1].id if rows else after_id


def _delete_unconfirmed(session: Session, *, before: datetime, limit: int) -> int:
    """Delete one batch of addresses that were submitted and never confirmed.

    Deleted rather than pseudonymised: an address that never used its confirmation link
    consented to nothing, so there is no record of consent worth keeping and no statistic the
    project is entitled to derive from it.

    Keyset pagination would be wrong here — the rows are removed, so the next query naturally
    starts where this one left off, and a cursor would skip whatever a concurrent write had
    inserted below it.

    Args:
        session: Open session. The caller commits.
        before: Rows created strictly before this instant have had their chance.
        limit: Batch size.

    Returns:
        How many rows were deleted.
    """
    rows = session.scalars(
        select(Subscriber)
        .where(
            Subscriber.status == SubscriberStatus.PENDING,
            Subscriber.confirmed_at.is_(None),
            Subscriber.created_at < before,
        )
        .order_by(Subscriber.id)
        .limit(limit)
    ).all()
    for row in rows:
        session.delete(row)
    return len(rows)


def run_purge(
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> PurgeReport:
    """Apply whichever retention rules a deployment has configured.

    Does nothing at all when neither period is set, which is the state issue #75 leaves the
    project in until the Law group answers. That is deliberate: the alternative is a default
    that quietly becomes the policy.

    Args:
        now: The current instant, for tests.
        settings: Validated settings. Defaults to the process-wide settings.
        session_factory: Factory for the per-batch sessions. Defaults to the process-wide one.

    Returns:
        The :class:`PurgeReport`, which distinguishes "nothing matched" from "no rule".
    """
    resolved = settings if settings is not None else get_settings()
    factory = session_factory if session_factory is not None else get_session_factory()
    moment = now if now is not None else utcnow()

    retention_days = resolved.subscriber_retention_days
    expiry_days = resolved.subscriber_unconfirmed_expiry_days
    report = PurgeReport(
        retention_enforced=retention_days is not None,
        expiry_enforced=expiry_days is not None,
    )
    if retention_days is None and expiry_days is None:
        log.info("subscriber purge: no retention rule is configured, so nothing was applied")
        return report

    stop = StopRequest()
    with stop:
        if retention_days is not None:
            horizon = moment - timedelta(days=retention_days)
            cursor = 0
            while not stop.requested:
                with session_scope(factory) as session:
                    dropped, cursor = _drop_expired_digests(
                        session, before=horizon, after_id=cursor, limit=_BATCH_SIZE
                    )
                if dropped == 0:
                    break
                report.digests_dropped += dropped

        if expiry_days is not None:
            deadline = moment - timedelta(days=expiry_days)
            while not stop.requested:
                with session_scope(factory) as session:
                    deleted = _delete_unconfirmed(session, before=deadline, limit=_BATCH_SIZE)
                if deleted == 0:
                    break
                report.unconfirmed_deleted += deleted

        report.interrupted = stop.requested

    log.info(
        "subscriber purge finished",
        extra={
            "context": {
                "digests_dropped": report.digests_dropped,
                "unconfirmed_deleted": report.unconfirmed_deleted,
                "interrupted": report.interrupted,
            }
        },
    )
    return report
