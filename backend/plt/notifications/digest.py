"""The weekly digest: one message per confirmed subscriber, listing what is new.

Driven by ``plt digest`` and by the scheduled workflow, which is a trigger and never a second
implementation — the same command a server cron calls (``docs/architecture.md`` section 7).

Four properties are requirements rather than incidental, and each mirrors the ingestion
runner's, because a send to a list is the same kind of long unattended loop as a scan:

**Streaming and batched.** Recipients are read one batch at a time with keyset pagination and
each batch commits on its own. Peak memory is one batch, whether the list holds ten addresses
or ten thousand, and the corpus for the window is read once and shared by every message.

**Resumable, and the window is what identifies a send.** ``subscriber.last_digest_at`` holds
the end of the last window an address was sent, and a run only considers addresses whose
position is behind its own ``until``. Re-running *the same window* therefore sends to exactly
the recipients the interrupted attempt had not reached, and to nobody twice — which is why
both the scheduled job and anyone resuming by hand pass an explicit ``--until``. A run with a
later ``until`` is a new window by definition, and a case that is still inside it will be
listed again; that is a re-send by request, not a duplicate.

**Interruptible.** ``SIGINT``/``SIGTERM`` sets a flag through
:class:`plt.utils.shutdown.StopRequest`; the message in flight is finished, its batch commits,
and the process exits cleanly.

**A failure is per recipient.** One address the server refuses is logged and counted, and its
``last_digest_at`` is left alone so the next run retries it. It does not end the send.

**The window is the tracker's own, not the courts'.** Cases are selected on ``first_seen_at``
— what is new *to the tracker* — because a judgment from 2019 that the pipeline reached this
week is news to a reader and one delivered yesterday that has been in the database a month is
not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from plt.config import Settings, get_settings
from plt.db.base import utcnow
from plt.db.models import Case, Subscriber
from plt.db.repositories import (
    cases_first_seen,
    confirmed_subscribers_after,
    count_cases_first_seen,
)
from plt.db.session import get_session_factory, session_scope
from plt.notifications.mailer import Mailer, MailError, build_mailer
from plt.notifications.messages import digest_message
from plt.utils.logging import get_logger
from plt.utils.shutdown import StopRequest

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session, sessionmaker

__all__ = ["DigestReport", "run_digest"]

log = get_logger(__name__)


@dataclass(slots=True)
class DigestReport:
    """What one digest run did.

    Accumulated in place as the run goes, so a run that was interrupted or that failed still
    reports what it had managed by then.

    Attributes:
        since: Inclusive start of the window.
        until: Exclusive end of the window.
        case_count: Cases the window held in all.
        listed: Cases actually listed in the message, bounded by ``digest_max_cases``.
        sent: Messages delivered.
        failed: Recipients the backend refused. Their position is not advanced.
        dry_run: Whether anything was sent at all.
        interrupted: Whether a signal ended the run early.
    """

    since: datetime
    until: datetime
    case_count: int = 0
    listed: int = 0
    sent: int = 0
    failed: int = 0
    dry_run: bool = False
    interrupted: bool = False
    cases: tuple[Case, ...] = field(default_factory=tuple, repr=False)

    def summary(self) -> str:
        """Return a one-line summary for the CLI and the run log."""
        mode = "dry run; " if self.dry_run else ""
        state = "interrupted; " if self.interrupted else ""
        return (
            f"digest {self.since.date().isoformat()}..{self.until.date().isoformat()}: "
            f"{mode}{state}{self.case_count} new case(s), "
            f"{self.sent} message(s) sent, {self.failed} failed"
        )


def _resolve_window(
    since: datetime | None,
    until: datetime | None,
    settings: Settings,
) -> tuple[datetime, datetime]:
    """Work out the window a run covers.

    Args:
        since: Lower bound the caller asked for, or ``None`` for the configured period.
        until: Upper bound, or ``None`` for now.
        settings: Settings supplying ``digest_period_days``.

    Returns:
        The inclusive lower and exclusive upper bounds.

    Raises:
        ValueError: If the window is empty or inverted, which would send a digest of nothing
            to everyone and advance their position past a period nobody read.
    """
    end = until if until is not None else utcnow()
    start = since if since is not None else end - timedelta(days=settings.digest_period_days)
    if start >= end:
        message = f"the digest window is empty: since={start.isoformat()} until={end.isoformat()}"
        raise ValueError(message)
    return start, end


def _load_window(session: Session, settings: Settings, report: DigestReport) -> None:
    """Read the cases the window holds, once, for every recipient.

    Args:
        session: Open database session.
        settings: Settings supplying ``digest_max_cases``.
        report: The report to fill in.
    """
    report.case_count = count_cases_first_seen(session, since=report.since, until=report.until)
    if report.case_count == 0:
        return
    report.cases = cases_first_seen(
        session,
        since=report.since,
        until=report.until,
        limit=settings.digest_max_cases,
    )
    report.listed = len(report.cases)


def _send_batch(
    settings: Settings,
    mailer: Mailer,
    report: DigestReport,
    batch: Sequence[Subscriber],
    stop: StopRequest,
) -> None:
    """Send one batch of messages, stamping each recipient as it succeeds.

    The rows belong to the caller's session, which commits the batch, so a stamp written here
    is durable exactly when the batch it belongs to is.

    Args:
        settings: Validated settings.
        mailer: Backend to send through.
        report: The report to accumulate into.
        batch: The subscribers in this batch.
        stop: The shutdown flag.
    """
    for subscriber in batch:
        address = subscriber.email
        if address is None:
            # Unreachable through the schema — a row without an address is unsubscribed, and
            # the query selects confirmed rows — but the address became optional when
            # unsubscribing started replacing it with a digest (core document 2.12), and a
            # send loop is the wrong place to learn that the invariant slipped. Skipped and
            # reported rather than crashed: one impossible row must not stop a whole digest.
            log.warning(
                "a confirmed subscriber holds no address and was skipped",
                extra={"context": {"subscriber_id": subscriber.id}},
            )
            continue
        message = digest_message(
            address,
            subscriber.token_seed,
            report.cases,
            settings,
            since=report.since,
            until=report.until,
            total=report.case_count,
        )
        if report.dry_run:
            report.sent += 1
        else:
            try:
                mailer.send(message)
            except MailError:
                report.failed += 1
                # No stamp: the position is not advanced over a message that did not go, so
                # the next run retries this address rather than skipping the window for it.
                log.warning(
                    "digest could not be delivered",
                    extra={"context": {"subscriber_id": subscriber.id}},
                )
                continue
            report.sent += 1
            subscriber.last_digest_at = report.until
            # Counted here rather than derived later, because the address this row will one
            # day be reduced to a digest of cannot be counted from (core document 2.12).
            subscriber.digest_count += 1
        if stop.requested:
            return


def run_digest(
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    dry_run: bool = False,
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    mailer: Mailer | None = None,
) -> DigestReport:
    """Send the digest for one window.

    Args:
        since: Inclusive start of the window. Defaults to ``digest_period_days`` before the
            end, which is what the weekly job uses.
        until: Exclusive end. Defaults to now.
        dry_run: Render every message and send none. Nothing is written to the database, so a
            dry run can be repeated and the real send afterwards is unaffected.
        settings: Validated settings. Defaults to the process-wide settings.
        session_factory: Factory for the per-batch sessions. Defaults to the process-wide one.
        mailer: Backend to send through. Defaults to the configured one, which is the console
            backend unless a deployment said otherwise.

    Returns:
        The :class:`DigestReport`.

    Raises:
        ValueError: If the window is empty or inverted.
    """
    resolved = settings if settings is not None else get_settings()
    factory = session_factory if session_factory is not None else get_session_factory()
    start, end = _resolve_window(since, until, resolved)
    report = DigestReport(since=start, until=end, dry_run=dry_run)

    owns_mailer = mailer is None
    backend = mailer if mailer is not None else build_mailer(resolved)
    stop = StopRequest()
    try:
        with stop:
            with session_scope(factory) as session:
                _load_window(session, resolved, report)

            if report.case_count == 0:
                # Silence is the right answer to a quiet week: a digest of nothing trains a
                # reader to ignore the next one, and it would advance every position past a
                # window that held no cases.
                log.info("digest skipped: the window held no new cases", extra=_context(report))
                return report

            cursor = 0
            while not stop.requested:
                with session_scope(factory) as session:
                    batch = confirmed_subscribers_after(
                        session,
                        after_id=cursor,
                        limit=resolved.digest_batch_size,
                        not_sent_since=report.until,
                    )
                    if not batch:
                        break
                    cursor = batch[-1].id
                    _send_batch(resolved, backend, report, batch, stop)
            report.interrupted = stop.requested
    finally:
        if owns_mailer:
            backend.close()

    log.info("digest finished", extra=_context(report))
    return report


def _context(report: DigestReport) -> dict[str, object]:
    """Build the structured-logging context for a digest run.

    Recipients are counted, never named: an address is personal data and does not belong in a
    log (``docs/architecture.md`` rule 2.7).

    Args:
        report: The run's report.

    Returns:
        The context mapping.
    """
    return {
        "context": {
            "since": report.since.isoformat(),
            "until": report.until.isoformat(),
            "cases": report.case_count,
            "listed": report.listed,
            "sent": report.sent,
            "failed": report.failed,
            "dry_run": report.dry_run,
            "interrupted": report.interrupted,
        }
    }
