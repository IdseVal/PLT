"""The orchestrator: one jurisdiction, one window, one run.

:func:`run_jurisdiction` drives the stage order fixed in ``docs/architecture.md`` section 4::

    discover -> dedup pre-check -> fetch -> normalise -> filter chain -> persist -> checkpoint

and it is the one module in the pipeline that may not know which jurisdiction it is running.
Every jurisdiction-specific decision belongs to a connector or to a keyword list; anything
about the Netherlands or the EU that leaked in here would have to be written again for the
next jurisdiction, which is exactly what this design exists to prevent.

Four properties are worth stating outright, because each is a requirement rather than an
implementation detail.

**Streaming.** ``discover`` is consumed lazily and sliced into batches of
``pipeline_batch_size``; each batch is processed in its own session and committed on its own.
Peak memory is one batch of candidates plus the document in hand, whatever the size of the
window — a run over five thousand documents holds no more than a run over fifty.

**Interruptibility.** ``SIGINT`` (and ``SIGTERM``) sets a flag rather than raising. The
document in flight is finished, its batch is committed, the checkpoint is written, the run
row is closed as ``interrupted`` and the process exits cleanly. A second signal falls through
to the default handler, so an impatient operator can still force the issue.

**Checkpoint safety.** The position advances only over documents that were processed
successfully *and* committed, and it stops advancing at the first document that failed, so
nothing after a failure is ever considered done. A failed run writes no checkpoint at all
(section 7). The consequence is deliberate: a document that fails every week holds the window
open and keeps appearing in the error count, which is how an operator finds out about it,
rather than the pipeline quietly stepping over it forever.

**Per-document isolation.** Each document is processed inside a savepoint. One that cannot be
fetched, parsed or persisted is rolled back on its own, logged with its identifier and
counted; the rest of the batch is unaffected, and one malformed judgment never ends a run.

**Auditability.** The counters are accumulated in place on the report the caller is handed,
not assigned once the run is over, so a run that failed or was interrupted records what it
had done by then. ``ingest_run`` exists to make a run readable afterwards, and a failed run
is the one most likely to be read: an operator has to be able to tell a run that fetched
eight hundred documents and then stalled from one that never started.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator, Sequence
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from types import FrameType, TracebackType
from typing import TYPE_CHECKING, Any, Self

from plt.config import Settings, get_settings
from plt.db.base import utcnow
from plt.db.models import IngestRun, IngestStatus, Jurisdiction
from plt.db.session import get_session_factory, session_scope
from plt.pipeline.base import (
    Candidate,
    DocumentUnavailableError,
    NormalisedCase,
    PipelineError,
    SourceConnector,
    SourceUnavailableError,
)
from plt.pipeline.checkpoint import Checkpoint, read_checkpoint, resolve_since, write_checkpoint
from plt.pipeline.dedup import (
    DedupAction,
    decide_after_fetch,
    decide_before_fetch,
    resolve_content_hash,
)
from plt.pipeline.filters.base import FilterChain, FilterResult
from plt.pipeline.filters.keywords import KeywordFilter
from plt.pipeline.persistence import PersistOutcome, persist_case, touch_last_seen
from plt.pipeline.registry import connector_for
from plt.pipeline.report import MatchReport, default_report_path
from plt.utils.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

__all__ = [
    "IngestCounters",
    "IngestReport",
    "run_jurisdiction",
]

log = get_logger(__name__)

#: Longest error message stored on ``ingest_run.error_message``.
_ERROR_MESSAGE_LIMIT = 2000


@dataclass(slots=True)
class IngestCounters:
    """What a run did, counted.

    These are the numbers written to ``ingest_run`` and printed by the CLI: a report to the
    content manager rather than debug output. They add up — every candidate discovered ends
    in exactly one of ``skipped_duplicate``, ``rejected``, ``inserted``, ``updated`` or
    ``errors``.

    They count what the run *did*, which on a run that ended badly includes the batch that
    was in flight and therefore rolled back. That is deliberate: the fetches happened and
    cost the source the requests, and the checkpoint — not these counters — is the record of
    what was committed.

    Attributes:
        discovered: Candidates ``discover`` yielded.
        fetched: Documents actually downloaded. The gap to ``discovered`` is the work
            deduplication saved, and the reason the pre-check exists.
        matched: Documents the filter chain passed.
        rejected: Documents the filter chain judged out of scope.
        inserted: Cases newly stored.
        updated: Stored cases revised in place.
        skipped_duplicate: Documents already stored and unchanged.
        errors: Documents that failed and were skipped.
    """

    discovered: int = 0
    fetched: int = 0
    matched: int = 0
    rejected: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_duplicate: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        """Return the counters as a plain mapping, for structured logging."""
        return asdict(self)


@dataclass(slots=True)
class IngestReport:
    """The outcome of one run, returned to the caller and mirrored in ``ingest_run``.

    Attributes:
        jurisdiction_code: Jurisdiction the run covered.
        connector: Connector that ran.
        status: Terminal state of the run.
        started_at: When the run began.
        finished_at: When it ended, or ``None`` while it is still going.
        counters: The counts above. The run accumulates into this object as it goes, so a
            run that failed or was interrupted still reports what it managed.
        checkpoint_before: Position the run started from.
        checkpoint_after: Position it left behind; identical to ``checkpoint_before`` when
            the run failed.
        window_since: Lower bound actually used, after the checkpoint was consulted.
        window_until: Upper bound requested, if any.
        dry_run: Whether the database was left untouched.
        run_id: Primary key of the ``ingest_run`` row; ``None`` for a dry run.
        report_path: Match report written, if one was.
        error_message: First fatal error, if the run failed.
    """

    jurisdiction_code: str
    connector: str
    status: IngestStatus = IngestStatus.RUNNING
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    counters: IngestCounters = field(default_factory=IngestCounters)
    checkpoint_before: Checkpoint | None = None
    checkpoint_after: Checkpoint | None = None
    window_since: datetime | None = None
    window_until: datetime | None = None
    dry_run: bool = False
    run_id: int | None = None
    report_path: Path | None = None
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        """Return whether the run completed without a fatal error."""
        return self.status in {IngestStatus.SUCCESS, IngestStatus.PARTIAL}

    @property
    def interrupted(self) -> bool:
        """Return whether the run stopped on a signal."""
        return self.status is IngestStatus.INTERRUPTED

    def summary(self) -> str:
        """Return a one-line summary for the CLI and the run log."""
        counts = self.counters
        return (
            f"{self.jurisdiction_code}/{self.connector}: {self.status.value}; "
            f"discovered {counts.discovered}, fetched {counts.fetched}, "
            f"matched {counts.matched}, inserted {counts.inserted}, "
            f"updated {counts.updated}, skipped {counts.skipped_duplicate}, "
            f"rejected {counts.rejected}, errors {counts.errors}"
        )


class _StopRequest:
    """A graceful-shutdown flag, set by ``SIGINT``/``SIGTERM`` while a run is in progress.

    Trapping the signal beats letting ``KeyboardInterrupt`` propagate: the interrupt would
    land in the middle of a batch, roll it back and throw away work the run had already done.
    With a flag, the document in flight finishes, its batch commits, and the checkpoint
    records exactly what was committed.

    Handlers can only be installed from the main thread; anywhere else the guard degrades to
    a no-op and the caller's own ``KeyboardInterrupt`` handling applies.
    """

    def __init__(self, signals: Sequence[int] | None = None) -> None:
        """Prepare the guard.

        Args:
            signals: Signal numbers to trap. Defaults to ``SIGINT`` and, where the platform
                has it, ``SIGTERM`` — what a container runtime or a cancelled CI job sends.
        """
        if signals is None:
            signals = [signal.SIGINT, *([signal.SIGTERM] if hasattr(signal, "SIGTERM") else [])]
        self._signals = tuple(signals)
        self._previous: dict[int, Any] = {}
        self._requested = False

    @property
    def requested(self) -> bool:
        """Return whether a shutdown has been requested."""
        return self._requested

    def __call__(self) -> bool:
        """Return whether a shutdown has been requested, as a predicate.

        Passed to :class:`plt.pipeline.http.PoliteClient`, so a backoff sleep is abandoned
        as soon as the run is asked to stop.
        """
        return self._requested

    def request(self, reason: str) -> None:
        """Request a graceful shutdown.

        Args:
            reason: What asked for it, for the log line.
        """
        if not self._requested:
            log.warning(
                "shutdown requested; finishing the document in flight",
                extra={"context": {"reason": reason}},
            )
        self._requested = True

    def _handle(self, signal_number: int, frame: FrameType | None) -> None:
        """Handle a trapped signal.

        The first requests a graceful stop; a second restores the previous handler and
        re-raises, so ``Ctrl+C`` twice still stops the process at once.

        Args:
            signal_number: The signal received.
            frame: The interrupted stack frame, unused.
        """
        del frame
        if self._requested:
            self._restore()
            signal.raise_signal(signal_number)
            return
        self.request(f"signal {signal.Signals(signal_number).name}")

    def _restore(self) -> None:
        """Put the previous signal handlers back."""
        for number, handler in self._previous.items():
            signal.signal(number, handler)
        self._previous.clear()

    def __enter__(self) -> Self:
        """Install the handlers, if this is the main thread."""
        if threading.current_thread() is not threading.main_thread():
            return self
        for number in self._signals:
            try:
                self._previous[number] = signal.signal(number, self._handle)
            except (ValueError, OSError):  # pragma: no cover - platform dependent
                log.debug("could not trap signal", extra={"context": {"signal": number}})
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Restore the previous handlers."""
        del exc_type, exc, traceback
        self._restore()


def _batched(candidates: Iterator[Candidate], size: int) -> Iterator[list[Candidate]]:
    """Slice a candidate stream into batches without materialising the stream.

    Args:
        candidates: The stream from ``discover``.
        size: Batch size; one transaction per batch.

    Yields:
        Lists of at most ``size`` candidates.
    """
    batch: list[Candidate] = []
    for candidate in candidates:
        batch.append(candidate)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


class _Run:
    """One execution of one connector over one window.

    Holds the state a run accumulates — counters, the pending and committed checkpoint
    positions, whether a document has failed — so :func:`run_jurisdiction` stays a readable
    sequence of stages.

    The counters are the caller's object rather than one of this class's own, and are
    incremented in place. Handing them over at construction is what makes them survive a
    failure: there is no assignment after the run for an exception to skip past, so a run
    that fetched eight hundred documents and then died still says so (section 4.4).
    """

    def __init__(
        self,
        connector: SourceConnector,
        chain: FilterChain,
        session_factory: sessionmaker[Session],
        *,
        counters: IngestCounters,
        dry_run: bool,
        batch_size: int,
        report: MatchReport | None,
        stop: _StopRequest,
    ) -> None:
        """Bind the run to its collaborators.

        Args:
            connector: The connector to run.
            chain: The filter chain to judge documents with.
            session_factory: Factory the per-batch sessions come from.
            counters: Counters to accumulate into, owned by the caller's report.
            dry_run: Whether to leave the database untouched.
            batch_size: Documents per transaction.
            report: Match report to write, or ``None``.
            stop: The graceful-shutdown flag.
        """
        self._connector = connector
        self._chain = chain
        self._factory = session_factory
        self._dry_run = dry_run
        self._batch_size = batch_size
        self._report = report
        self._stop = stop
        self.counters = counters
        self._pending = Checkpoint(
            connector=connector.name, jurisdiction_code=connector.jurisdiction_code
        )
        self._committed = self._pending
        self._error_seen = False

    @property
    def committed_checkpoint(self) -> Checkpoint:
        """Return the position covering only work that has been committed."""
        return self._committed

    def start_from(self, checkpoint: Checkpoint | None) -> None:
        """Seed the run's position from the stored checkpoint.

        Args:
            checkpoint: The stored position, or ``None`` on a first run.
        """
        if checkpoint is not None:
            self._pending = checkpoint
            self._committed = checkpoint

    def execute(self, since: datetime | None, until: datetime | None) -> None:
        """Process the whole window, batch by batch.

        Args:
            since: Lower bound of the window.
            until: Upper bound of the window.
        """
        for batch in _batched(self._connector.discover(since, until), self._batch_size):
            self.counters.discovered += len(batch)
            with session_scope(self._factory) as session:
                self._process_batch(session, batch)
            # Reached only once the batch has committed, which is what makes promoting the
            # position safe: the checkpoint never claims work that was rolled back.
            self._committed = self._pending
            if self._stop.requested:
                return

    def _process_batch(self, session: Session, batch: Sequence[Candidate]) -> None:
        """Process one batch inside one transaction, one savepoint per document.

        Args:
            session: Session for this batch; committed by the caller's ``session_scope``.
            batch: The candidates to process.

        Raises:
            SourceUnavailableError: If the source becomes unusable, which ends the run.
        """
        for candidate in batch:
            try:
                with session.begin_nested():
                    self._process(session, candidate)
            except SourceUnavailableError:
                raise
            except Exception as error:
                self._record_failure(candidate, error)
            if self._stop.requested:
                return

    def _record_failure(self, candidate: Candidate, error: Exception) -> None:
        """Log and count a document that failed, and stop advancing the checkpoint.

        Args:
            candidate: The candidate that failed.
            error: What went wrong.
        """
        self.counters.errors += 1
        self._error_seen = True
        context = {
            "jurisdiction": candidate.jurisdiction_code,
            "source_id": candidate.source_id,
            "connector": self._connector.name,
            "error": f"{type(error).__name__}: {error}",
        }
        message = "document failed; skipping it and holding the checkpoint back"
        if isinstance(error, DocumentUnavailableError):
            # An expected, per-document outcome: a stack trace would be noise.
            log.warning(message, extra={"context": context})
        else:
            log.exception(message, extra={"context": context})

    def _process(self, session: Session, candidate: Candidate) -> None:
        """Run one candidate through every stage.

        Args:
            session: Session for the current batch.
            candidate: The candidate to process.
        """
        before = decide_before_fetch(session, candidate)
        if before.action is DedupAction.SKIP:
            self._skip_unchanged(session, candidate, before.case_id, before.reason)
            return

        raw = self._connector.fetch(candidate)
        self.counters.fetched += 1
        case = self._connector.normalise(raw)
        self._check_identity(candidate, case)

        content_hash = resolve_content_hash(candidate, case)
        after = decide_after_fetch(before, content_hash)
        if after.action is DedupAction.SKIP:
            self._skip_unchanged(session, candidate, after.case_id, after.reason)
            return

        result = self._chain.evaluate(case)
        if not result.passed:
            self._reject(case, result, candidate, content_hash)
            return

        self.counters.matched += 1
        action = after.action.value
        if not self._dry_run:
            outcome = persist_case(
                session, case, result, content_hash=content_hash, case_id=after.case_id
            )
            action = outcome.value
            if outcome is PersistOutcome.INSERTED:
                self.counters.inserted += 1
            else:
                self.counters.updated += 1
            log.info(
                "case %s",
                outcome.value,
                extra={
                    "context": {
                        "jurisdiction": case.jurisdiction_code,
                        "source_id": case.source_id,
                        "score": round(result.score, 3),
                        "terms": len(result.matches),
                    }
                },
            )
        self._record(case, result, action=action, content_hash=content_hash, at=candidate)
        self._advance(candidate)

    def _reject(
        self,
        case: NormalisedCase,
        result: FilterResult,
        candidate: Candidate,
        content_hash: str,
    ) -> None:
        """Count and report a document the filter chain judged out of scope.

        Args:
            case: The normalised case.
            result: The rejecting verdict.
            candidate: The candidate it came from.
            content_hash: The resolved revision fingerprint.
        """
        self.counters.rejected += 1
        log.debug(
            "document filtered out",
            extra={
                "context": {
                    "source_id": case.source_id,
                    "score": round(result.score, 3),
                    "stage": result.stage,
                }
            },
        )
        self._record(case, result, action="reject", content_hash=content_hash, at=candidate)
        self._advance(candidate)

    def _check_identity(self, candidate: Candidate, case: NormalisedCase) -> None:
        """Verify that the connector normalised the document it was asked to.

        A mismatch would file one document's content under another's identifier, which the
        unique key cannot catch. Cheap to check here, impossible to notice later.

        Args:
            candidate: The candidate that was fetched.
            case: The case the connector returned.

        Raises:
            PipelineError: If the identifiers do not line up.
        """
        if case.source_id != candidate.source_id:
            message = (
                f"connector {self._connector.name} normalised {case.source_id!r} while "
                f"fetching {candidate.source_id!r}"
            )
            raise PipelineError(message)
        if case.jurisdiction_code != candidate.jurisdiction_code:
            message = (
                f"connector {self._connector.name} returned jurisdiction "
                f"{case.jurisdiction_code!r} for a {candidate.jurisdiction_code!r} candidate"
            )
            raise PipelineError(message)

    def _skip_unchanged(
        self,
        session: Session,
        candidate: Candidate,
        case_id: int | None,
        reason: str,
    ) -> None:
        """Record that a stored document is unchanged, touching only ``last_seen_at``.

        Args:
            session: Session for the current batch.
            candidate: The candidate that was skipped.
            case_id: Primary key of the stored case.
            reason: Why it was skipped, for the log.
        """
        self.counters.skipped_duplicate += 1
        if not self._dry_run and case_id is not None:
            touch_last_seen(session, case_id)
        log.debug(
            "document unchanged",
            extra={"context": {"source_id": candidate.source_id, "reason": reason}},
        )
        self._advance(candidate)

    def _advance(self, candidate: Candidate) -> None:
        """Move the pending checkpoint over a document processed successfully.

        Args:
            candidate: The candidate just processed.
        """
        if self._error_seen:
            return
        self._pending = self._pending.advanced_to(
            modified_at=candidate.modified_at,
            cursor=candidate.cursor,
            source_id=candidate.source_id,
        )

    def _record(
        self,
        case: NormalisedCase,
        result: FilterResult,
        *,
        action: str,
        content_hash: str,
        at: Candidate,
    ) -> None:
        """Append a line to the match report, if one is being written.

        Args:
            case: The case that was judged.
            result: The verdict.
            action: What the run did, or would have done.
            content_hash: The resolved revision fingerprint.
            at: The candidate, for its modification timestamp.
        """
        if self._report is not None:
            self._report.record(
                case,
                result,
                action=action,
                content_hash=content_hash,
                modified_at=at.modified_at,
            )


def _default_chain(jurisdiction_code: str, settings: Settings) -> FilterChain:
    """Build the filter chain for a jurisdiction.

    Stage 1 is the curated keyword matcher. Later stages append here and nowhere else, which
    is what "the chain is pluggable" means in practice (``docs/core-document.md`` 2.5).

    Args:
        jurisdiction_code: Jurisdiction whose list to load.
        settings: Settings resolving the keywords directory.

    Returns:
        The chain to judge this jurisdiction's documents with.
    """
    return FilterChain.of(KeywordFilter.for_jurisdiction(jurisdiction_code, settings=settings))


def _open_run_row(session: Session, connector: SourceConnector, report: IngestReport) -> int:
    """Insert the ``ingest_run`` row for a run that is starting.

    Args:
        session: Open database session.
        connector: The connector about to run.
        report: The in-memory report, supplying the start time and the prior checkpoint.

    Returns:
        The primary key of the inserted row.
    """
    row = IngestRun(
        jurisdiction_code=connector.jurisdiction_code,
        connector=connector.name,
        started_at=report.started_at,
        status=IngestStatus.RUNNING,
        dry_run=report.dry_run,
        checkpoint_before=(
            report.checkpoint_before.as_dict() if report.checkpoint_before is not None else None
        ),
    )
    session.add(row)
    session.flush()
    return row.id


def _close_run_row(session: Session, report: IngestReport) -> None:
    """Write the final counts and status onto the ``ingest_run`` row.

    Args:
        session: Open database session.
        report: The finished report.
    """
    if report.run_id is None:
        return
    row = session.get(IngestRun, report.run_id)
    if row is None:  # pragma: no cover - this run inserted the row
        log.warning("ingest_run row disappeared", extra={"context": {"run_id": report.run_id}})
        return
    counters = report.counters
    row.status = report.status
    row.finished_at = report.finished_at
    row.fetched_count = counters.fetched
    row.matched_count = counters.matched
    row.inserted_count = counters.inserted
    row.updated_count = counters.updated
    row.skipped_duplicate_count = counters.skipped_duplicate
    row.error_count = counters.errors
    row.checkpoint_after = (
        report.checkpoint_after.as_dict() if report.checkpoint_after is not None else None
    )
    row.error_message = report.error_message


def _require_known_jurisdiction(session: Session, code: str) -> None:
    """Check that the jurisdiction exists before anything references it.

    Args:
        session: Open database session.
        code: Jurisdiction code.

    Raises:
        PipelineError: If the jurisdiction is unknown. The foreign keys on ``ingest_run`` and
            ``ingest_checkpoint`` would fail later anyway, with a far worse message.
    """
    jurisdiction = session.get(Jurisdiction, code)
    if jurisdiction is None:
        message = (
            f"jurisdiction {code!r} is not in the database; seed it with a migration before "
            "ingesting it"
        )
        raise PipelineError(message)
    if not jurisdiction.is_active:
        log.warning(
            "ingesting a jurisdiction marked inactive",
            extra={"context": {"jurisdiction": code}},
        )


def _final_status(counters: IngestCounters, *, stopped: bool) -> IngestStatus:
    """Decide the terminal status of a completed run.

    Args:
        counters: The run's counts.
        stopped: Whether a shutdown was requested.

    Returns:
        ``interrupted`` when a signal ended it, ``partial`` when documents failed,
        ``success`` otherwise.
    """
    if stopped:
        return IngestStatus.INTERRUPTED
    if counters.errors:
        return IngestStatus.PARTIAL
    return IngestStatus.SUCCESS


def _write_checkpoint_if_moved(session: Session, report: IngestReport) -> None:
    """Store the run's position, unless it did not move.

    Args:
        session: Open database session.
        report: The finished report.
    """
    position = report.checkpoint_after
    if position is None or position.is_empty or position == report.checkpoint_before:
        return
    write_checkpoint(session, position)


def run_jurisdiction(
    code: str,
    since: datetime | None = None,
    until: datetime | None = None,
    dry_run: bool = False,
    *,
    connector: SourceConnector | None = None,
    chain: FilterChain | None = None,
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    batch_size: int | None = None,
    report_path: Path | None = None,
) -> IngestReport:
    """Ingest one jurisdiction's window, from discovery through to the checkpoint.

    Args:
        code: Jurisdiction code, e.g. ``NL`` or ``EU``. Case-insensitive.
        since: Lower bound of the window. ``None`` lets the stored checkpoint supply it,
            which is how the weekly job runs (``docs/architecture.md`` section 7).
        until: Upper bound of the window, or ``None``.
        dry_run: Run every stage and write a match report, but make no database changes. The
            database is still read — deduplication needs it — and never written.
        connector: Connector to run. Defaults to the one registered for the jurisdiction.
        chain: Filter chain to use. Defaults to the jurisdiction's keyword matcher.
        settings: Validated settings. Defaults to the process-wide settings.
        session_factory: Factory for the per-batch sessions. Defaults to the process-wide
            factory.
        batch_size: Documents per transaction. Defaults to ``pipeline_batch_size``.
        report_path: Where to write the match report. Defaults to a timestamped file under
            ``pipeline_report_dir`` for a dry run, and to no report otherwise.

    Returns:
        The :class:`IngestReport`. A run that failed says so through ``status`` and
        ``error_message`` rather than by raising, so ``--all`` carries on to the next
        jurisdiction.

    Raises:
        ConnectorNotFoundError: If no connector serves the jurisdiction.
        KeywordListNotFoundError: If the jurisdiction has no keyword list. Both are caller
            errors, raised before the run starts.
    """
    resolved = settings if settings is not None else get_settings()
    jurisdiction_code = code.strip().upper()
    owns_connector = connector is None
    source = connector if connector is not None else connector_for(jurisdiction_code, resolved)
    filters = chain if chain is not None else _default_chain(source.jurisdiction_code, resolved)
    factory = session_factory if session_factory is not None else get_session_factory()
    size = batch_size if batch_size is not None else resolved.pipeline_batch_size

    report = IngestReport(
        jurisdiction_code=source.jurisdiction_code,
        connector=source.name,
        dry_run=dry_run,
        window_until=until,
    )
    stop = _StopRequest()

    try:
        with session_scope(factory) as session:
            _require_known_jurisdiction(session, source.jurisdiction_code)
            report.checkpoint_before = read_checkpoint(session, source.name)
            report.checkpoint_after = report.checkpoint_before
            if not dry_run:
                report.run_id = _open_run_row(session, source, report)

        report.window_since = resolve_since(since, report.checkpoint_before)
        log.info(
            "ingestion started",
            extra={
                "context": {
                    "jurisdiction": report.jurisdiction_code,
                    "connector": report.connector,
                    "since": report.window_since.isoformat() if report.window_since else None,
                    "until": until.isoformat() if until else None,
                    "dry_run": dry_run,
                    "batch_size": size,
                    "stages": list(filters.stage_names),
                }
            },
        )

        with ExitStack() as stack:
            stack.enter_context(stop)
            match_report = _open_report(stack, report, resolved, report_path)
            run = _Run(
                source,
                filters,
                factory,
                counters=report.counters,
                dry_run=dry_run,
                batch_size=size,
                report=match_report,
                stop=stop,
            )
            run.start_from(report.checkpoint_before)
            try:
                run.execute(report.window_since, until)
            except KeyboardInterrupt:
                # Only reachable when no handler could be installed, e.g. off the main
                # thread. session_scope has already rolled the batch in flight back.
                stop.request("KeyboardInterrupt")
            report.status = _final_status(run.counters, stopped=stop.requested)
            report.checkpoint_after = run.committed_checkpoint
    except Exception as error:
        # The counters need no rescuing here: the run incremented them on report.counters
        # directly, which is the point of handing them over. Both assignments below are the
        # verdict rather than the tally, and the checkpoint is deliberately pinned to where
        # the run started — a failed run writes none (section 4.4).
        report.status = IngestStatus.FAILED
        report.error_message = f"{type(error).__name__}: {error}"[:_ERROR_MESSAGE_LIMIT]
        report.checkpoint_after = report.checkpoint_before
        log.exception(
            "ingestion failed; the checkpoint is left where it was",
            extra={
                "context": {
                    "jurisdiction": report.jurisdiction_code,
                    "connector": report.connector,
                }
            },
        )
    finally:
        if owns_connector:
            source.close()

    report.finished_at = utcnow()
    if not dry_run:
        with session_scope(factory) as session:
            if report.succeeded or report.interrupted:
                _write_checkpoint_if_moved(session, report)
            _close_run_row(session, report)

    log.info(
        "ingestion finished",
        extra={
            "context": {
                "jurisdiction": report.jurisdiction_code,
                "connector": report.connector,
                "status": report.status.value,
                **report.counters.as_dict(),
                "checkpoint_after": (
                    report.checkpoint_after.as_dict() if report.checkpoint_after else None
                ),
                "report": str(report.report_path) if report.report_path else None,
            }
        },
    )
    return report


def _open_report(
    stack: ExitStack,
    report: IngestReport,
    settings: Settings,
    report_path: Path | None,
) -> MatchReport | None:
    """Open the match report for a run, if one is wanted.

    A dry run always writes one — it is the whole output of the run. A live run writes one
    only when the caller named a path.

    Args:
        stack: Exit stack that closes the report when the run ends, interrupted or not.
        report: The run report, whose ``report_path`` is filled in.
        settings: Settings supplying the default report directory.
        report_path: Path the caller asked for, if any.

    Returns:
        The opened report, or ``None``.
    """
    if not report.dry_run and report_path is None:
        return None
    path = report_path or default_report_path(
        settings.pipeline_report_dir, report.jurisdiction_code, report.connector
    )
    report.report_path = path
    return stack.enter_context(
        MatchReport(path, jurisdiction_code=report.jurisdiction_code, connector=report.connector)
    )
