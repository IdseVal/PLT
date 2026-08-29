"""Targeted repair of a partial corpus: list, diff, fetch the difference.

A capture walks the source's discovery window. When it is interrupted — and one that runs for
days will be — the store is left holding most of a corpus, and the only way to close the gap
was to run the walk again from the beginning. Cases already on disk cost no *fetch*, which is
what made that look cheap, but **every discovery query is re-run**. For CELLAR that is a
``COUNT(DISTINCT ?celex)`` for every date window in seventy-four years, then a grouped page
query carrying two optional expression joins for every window that holds anything. Closing a
gap of 4,827 cases meant replaying thousands of the most expensive queries in the project over
the 99,259 already held; on 8 August 2026 that took 19,524 requests and three hours and
twenty-three minutes before the endpoint answered ``Retry-After: 1800``, and on the following
morning it was refused again after 206.

So this module asks a cheaper question, in three steps, and none of them is discovery:

1. **List the identifiers.** :meth:`~plt.pipeline.base.SourceConnector.enumerate_identifiers`
   — for CELLAR a ``SELECT DISTINCT ?celex`` with no aggregate and no join, for Rechtspraak
   the Atom feed that already lists ECLIs a thousand at a time.
2. **Diff against the store.** Local and free: ``metadata.json`` is written last and carries
   the identifier, so a stat call answers "do we hold this" without a request.
3. **Fetch the difference.** Through the connector's own ``fetch`` and
   :func:`~plt.pipeline.mirror.mirror_case`, so a repaired case is byte-for-byte the case a
   capture would have written.

Everything else is the mirror's: the store, the politeness of
:class:`~plt.pipeline.http.PoliteClient`, ``SIGINT`` handling, ``_failures.jsonl``, the
manifest, and one readable log per run. This module is the list, the diff, and nothing more.

Three properties are worth stating because they differ from a capture.

**Its position is not a window.** A repair writes ``_repair_checkpoint.json`` and never
``_checkpoint.json``. The capture's position is a high-water mark on the source's modification
dates and supplies the next capture's window; a repair's is a place in an identifier listing,
which as a window would mean nothing. Writing one over the other would make a capture resume
from a bound no run had walked to, and skip everything below it forever.

**A failed case does not hold anything back.** In a capture it must: the window advances past
what succeeded, so a failure that let the position move would be a case silently dropped. A
repair has nothing to advance past — the next repair recomputes the difference from what is on
disk, and a case that did not come down is simply still missing, so it is offered again.

**A repaired case records no discovery.** ``metadata.json`` carries ``source_modified_at``,
``source_revision`` and ``discovery_cursor`` from the candidate, and a listing states none of
the three, so on a repaired case all three are ``null`` where a captured case has values. That
is the honest record rather than a defect: nothing was discovered, so nothing should claim to
have been. None of it is *lost* — the source's own modification date is in the notice stored
beside it and in ``source_metadata.cellar_last_modification_date``, verbatim, which is where a
reader who needs it should look. The next capture to reach the case through its window fills
the fields in.

**What makes it resumable is the store, not the checkpoint.** A re-run lists again, finds the
cases fetched last time on disk, and skips them. That costs one cheap listing, which is the
entire premise of the mode; machinery to avoid re-listing would be machinery justified only by
an expensive listing, and if the listing were expensive this mode would not be worth having.
The checkpoint is written all the same, because a run that was interrupted should be able to
say where it got to.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from plt.config import Settings
from plt.db.models import IngestStatus
from plt.pipeline.base import SourceConnector, SourceUnavailableError
from plt.pipeline.checkpoint import Checkpoint
from plt.pipeline.mirror import (
    CorpusStore,
    MirrorReport,
    final_status,
    mirror_case,
    run_against_store,
)
from plt.pipeline.runlog import RunMode
from plt.utils.logging import get_logger
from plt.utils.shutdown import StopRequest

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    from plt.pipeline.base import Candidate

__all__ = ["repair_jurisdiction"]

log = get_logger(__name__)


def repair_jurisdiction(
    jurisdiction_code: str,
    since: datetime | None = None,
    until: datetime | None = None,
    *,
    settings: Settings | None = None,
    store_root: Path | None = None,
    limit: int | None = None,
    connector: SourceConnector | None = None,
) -> MirrorReport:
    """Fill in what a jurisdiction's corpus is missing, without walking discovery.

    Args:
        jurisdiction_code: Jurisdiction to repair, e.g. ``EU``.
        since: Narrow the identifier listing to what was modified at or after this instant.
            ``None`` — the usual case — lists everything the source holds and repairs the
            whole corpus. It is **not** taken from the stored position: a repair that resumed
            from a capture's checkpoint would look at exactly the part of the corpus that is
            not missing.
        until: Upper bound on the listing, on the same terms.
        settings: Validated settings. Defaults to the process-wide settings.
        store_root: Root of the case-law store. Defaults to ``corpus_store_dir``.
        limit: Stop after this many cases have been fetched. For a rehearsal, and for a first
            contact with a source that has been refusing us.
        connector: Connector to drive. Defaults to the one the registry holds for the
            jurisdiction; tests pass a fake.

    Returns:
        The run's report, in which ``discovered`` counts identifiers listed rather than cases
        offered in a window, and the difference between it and ``skipped`` is the gap.

    Raises:
        ConnectorNotFoundError: If no connector serves the jurisdiction.
        IdentifierListUnavailableError: If the connector has no listing cheaper than
            discovery, in which case a repair would cost more than the walk it replaces.
        CorpusStoreError: If the store cannot be opened or its position cannot be recorded.
    """

    def repair(
        source: SourceConnector,
        store: CorpusStore,
        report: MirrorReport,
        stop: StopRequest,
        resolved: Settings,
        cap: int | None,
    ) -> None:
        """Read the store's repair position, then list, diff and fetch."""
        report.checkpoint_before = store.read_repair_checkpoint(source.name)
        report.window_since = since
        log.info(
            "repairing a corpus",
            extra={
                "context": {
                    "jurisdiction": report.jurisdiction_code,
                    "connector": report.connector,
                    "store": str(store.path),
                    "since": since.isoformat() if since else None,
                    "until": until.isoformat() if until else None,
                    "already_held": store.count_cases(),
                }
            },
        )
        _repair(source, store, report, stop, resolved, cap)

    return run_against_store(
        jurisdiction_code,
        repair,
        mode=RunMode.REPAIR,
        until=until,
        settings=settings,
        store_root=store_root,
        limit=limit,
        connector=connector,
    )


def _repair(
    source: SourceConnector,
    store: CorpusStore,
    report: MirrorReport,
    stop: StopRequest,
    settings: Settings,
    limit: int | None,
) -> None:
    """Compare the source's identifier listing with the store, and fetch what is absent.

    Args:
        source: The connector to list and fetch through.
        store: The store to compare against and write to.
        report: The report to accumulate into, so a run that ends badly still records what it
            had done by then.
        stop: The graceful-shutdown flag, checked between cases so ``Ctrl+C`` finishes the one
            in flight and no more.
        settings: Validated settings, supplying how often the position is written.
        limit: Stop after this many cases have been fetched, or ``None``.
    """
    position = report.checkpoint_before or Checkpoint(
        connector=source.name, jurisdiction_code=report.jurisdiction_code
    )
    report.checkpoint_after = position
    every = settings.pipeline_batch_size
    since_written = 0
    try:
        for candidate in source.enumerate_identifiers(report.window_since, report.window_until):
            if stop.requested:
                break
            report.counters.discovered += 1
            # Counts the case as skipped when the store already holds it - which is the diff -
            # and otherwise fetches and writes it. A failure is recorded and the listing goes
            # on: unlike a window, a listing has no position for one bad case to hold back.
            mirror_case(source, store, candidate, report)
            position = _advanced(position, candidate)
            report.checkpoint_after = position
            since_written += 1
            if since_written >= every:
                store.write_repair_checkpoint(position)
                since_written = 0
            if limit is not None and report.counters.mirrored >= limit:
                log.info("repair limit reached", extra={"context": {"limit": limit}})
                break
    except SourceUnavailableError as error:
        report.status = IngestStatus.FAILED
        report.error_message = str(error)
        log.warning(
            "the source became unusable; the repair stops where it is",
            extra={"context": {"jurisdiction": report.jurisdiction_code, "error": str(error)}},
        )
    else:
        report.status = final_status(report, stopped=stop.requested)
    store.write_repair_checkpoint(position)


def _advanced(position: Checkpoint, candidate: Candidate) -> Checkpoint:
    """Move the repair's position onto the identifier just compared.

    The modification instant is deliberately not carried over, even when the listing states
    one. A repair's position is a place in a listing, and an instant sitting on it would read
    as a window that had been walked — which is the misreading that would let this file be
    mistaken for ``_checkpoint.json`` and a corpus be silently truncated.

    Args:
        position: The position so far.
        candidate: The identifier just compared with the store.

    Returns:
        The position, advanced.
    """
    return position.advanced_to(cursor=candidate.cursor, source_id=candidate.source_id)
