"""The record one mirror run leaves behind, written for a person rather than a parser.

``plt mirror`` is going onto a weekly schedule, which means nobody will be watching it. That
is the shape of failure ``docs/core-document.md`` section 2.7 calls the expensive one: a job
whose failure looks exactly like its success. A quiet week and a week where discovery
enumerated nothing both leave a store that has not changed; a run that gave up after four
hours leaves one too.

So every run writes one file::

    <corpus_store_dir>/EU/logs/2026-W32_20260809T031205Z.log

and it is meant to answer, on its own, the question an operator actually asks on a Monday
morning: *did last night's refresh work, and what did it bring in?* Nothing in it has to be
cross-referenced against the code, the database or the process log to be understood.

**The name.** ISO week first, then the exact UTC instant the run started, then ``.log``:

* the week is what the schedule is expressed in, and what the person reading is looking for;
* the instant means a weekly task can never overwrite last week's file, and two runs in the
  same week - a failure and the retry that fixed it - each keep their own record;
* both parts sort lexicographically into chronological order, ISO weeks being contiguous, so
  a plain directory listing is a history;
* the jurisdiction is the directory, so it is not repeated in every name.

**It is written whatever the outcome.** A log that only appears on success is useless for
exactly the failure it exists to catch, so the interrupted and failed paths write one too,
and a log that cannot be written is reported and swallowed: it is the record of the work, not
the work.

**A repair is logged as a repair.** ``plt mirror --repair`` leaves the same counters behind as
a capture and means something else by them: what it offered was an identifier listing rather
than a discovery window, and the number that matters is the difference between the listing and
the store. A log that presented the two identically would let a reader conclude a repair had
walked a window it never asked for, so :class:`RunMode` decides the headings and the wording.

**It is additive.** ``manifest.json``, ``_checkpoint.json`` and ``_failures.jsonl`` keep their
meanings exactly (``docs/architecture.md`` section 9). This file summarises the run's failures
and points at ``_failures.jsonl`` rather than reproducing it, because that file is the
store's whole history and this one is one night of it.
"""

from __future__ import annotations

import enum
import re
import textwrap
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from plt.config import Settings
from plt.db.models import IngestStatus
from plt.pipeline.checkpoint import Checkpoint
from plt.utils.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    from pathlib import Path

    from plt.pipeline.mirror import CorpusStore, MirrorReport

__all__ = [
    "LOG_DIR_NAME",
    "RunMode",
    "prune_run_logs",
    "render_run_log",
    "run_log_name",
    "write_run_log",
]


class RunMode(enum.StrEnum):
    """How a run against a corpus store got its list of cases.

    It lives here, beside the log, rather than beside the runs themselves because the log is
    what has to *read* it: a repair and a capture leave the same counters behind and mean
    different things by them, and a reader who cannot tell which they are looking at will
    misread both. :mod:`plt.pipeline.mirror` already imports this module for the log
    directory, so this costs no new dependency in either direction.

    Attributes:
        MIRROR: The capture: walk the source's discovery window and store what is new.
        REPAIR: The repair: list the source's identifiers, diff against the store, and fetch
            only what is missing (``docs/architecture.md`` rule 2.10).
    """

    MIRROR = "mirror"
    REPAIR = "repair"


log = get_logger(__name__)

#: Directory the run logs live in, inside the jurisdiction's own directory. Reserved by
#: :func:`plt.pipeline.mirror.case_folder_name`, so no case can be stored under this name.
LOG_DIR_NAME: Final[str] = "logs"

#: Names this module writes, and therefore the only ones retention may ever delete. A file an
#: operator put in the directory by hand does not match it and is left alone.
RUN_LOG_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{4}-W\d{2}_\d{8}T\d{6}Z(_\d+)?\.log$")

#: Failures listed individually. The rest are counted by kind and left to ``_failures.jsonl``;
#: a run where ten thousand cases failed needs a diagnosis, not ten thousand lines.
_FAILURES_LISTED: Final[int] = 20

#: Width the label column is padded to, so the values line up down the page.
_LABEL_WIDTH: Final[int] = 15

#: Longest line written. Narrow enough to read in Notepad on a half-width window, which is
#: where these will actually be opened.
_WIDTH: Final[int] = 96

#: Rendering of an instant. Deliberately not ``%A``/``%B``: those are locale-dependent, and a
#: log read on one machine should not be in a different language from the same log on another.
_INSTANT: Final[str] = "%Y-%m-%d %H:%M:%S UTC"

#: How each terminal state is explained to a reader who has not read the code.
_OUTCOMES: Final[dict[IngestStatus, str]] = {
    IngestStatus.SUCCESS: "success - the window was walked to the end",
    IngestStatus.PARTIAL: (
        "partial - the window was walked to the end, but cases failed, so the checkpoint "
        "stopped at the first of them"
    ),
    IngestStatus.INTERRUPTED: (
        "interrupted - a stop signal arrived (Ctrl+C, or the scheduled task being ended); "
        "the case in flight was finished and the position written"
    ),
    IngestStatus.FAILED: "failed - the run stopped before the end of its window",
    IngestStatus.RUNNING: "unknown - the run never recorded an outcome, which is a defect",
}

#: How a repair's terminal states are explained, where the capture's wording would be wrong:
#: a repair has no window to walk to the end of, and a case that failed does not hold a
#: position back, because the next repair re-derives the difference from the disk.
_REPAIR_OUTCOMES: Final[dict[IngestStatus, str]] = {
    IngestStatus.SUCCESS: "success - the whole identifier listing was compared with the store",
    IngestStatus.PARTIAL: (
        "partial - the whole listing was compared, but cases failed to come down; they are "
        "still missing, and the next repair will offer them again"
    ),
    IngestStatus.FAILED: "failed - the run stopped before the listing had been read to the end",
}


def iso_week(moment: datetime) -> str:
    """Return the ISO week a run belongs to.

    Args:
        moment: The instant the run started, in UTC.

    Returns:
        The week as ``YYYY-Www``, e.g. ``2026-W32``. ISO week-numbering years are contiguous,
        so these sort into chronological order even across a new year.
    """
    calendar = moment.isocalendar()
    return f"{calendar.year}-W{calendar.week:02d}"


def run_log_name(started_at: datetime) -> str:
    """Return the file name one run's log is written under.

    Args:
        started_at: When the run began. Rendered in UTC, whatever it carries.

    Returns:
        ``<ISO week>_<ISO basic instant>.log``, e.g. ``2026-W32_20260809T031205Z.log``.
    """
    moment = started_at.astimezone(UTC)
    return f"{iso_week(moment)}_{moment.strftime('%Y%m%dT%H%M%SZ')}.log"


def write_run_log(store: CorpusStore, report: MirrorReport, settings: Settings) -> Path | None:
    """Write one run's log into the store, and apply whatever retention is configured.

    Never raises. A store that cannot take the log is worth a warning, not the loss of a run
    that has already written its cases, its checkpoint and its failures to disk.

    Args:
        store: The store the run wrote to, which owns the directory.
        report: The finished run.
        settings: The validated settings the run used, for the configured rate and the
            retention horizon.

    Returns:
        The file written, or ``None`` when it could not be written.
    """
    try:
        path = store.write_run_log(
            run_log_name(report.started_at), render_run_log(report, settings)
        )
    except OSError as error:
        log.warning(
            "the mirror run log could not be written; the run itself is unaffected",
            extra={"context": {"store": str(store.path), "error": str(error)}},
        )
        return None
    log.info(
        "mirror run log written",
        extra={"context": {"path": str(path), "status": report.status.value}},
    )
    prune_run_logs(store, settings.corpus_log_retention_runs)
    return path


def prune_run_logs(store: CorpusStore, keep: int | None) -> int:
    """Delete the oldest run logs beyond the configured horizon.

    Args:
        store: The store whose log directory is pruned.
        keep: How many logs to keep, newest first. ``None`` - the default - keeps every log,
            because how long a record of an unattended job is worth keeping is the
            deployment's decision and a default here would be a policy nobody chose.

    Returns:
        How many files were deleted.
    """
    if keep is None:
        return 0
    existing = sorted(
        path for path in store.run_logs() if RUN_LOG_PATTERN.fullmatch(path.name) is not None
    )
    removed = 0
    for path in existing[: max(len(existing) - keep, 0)]:
        try:
            path.unlink()
        except OSError as error:  # pragma: no cover - the file was listed a moment ago
            log.warning(
                "an expired mirror run log could not be deleted",
                extra={"context": {"path": str(path), "error": str(error)}},
            )
            continue
        removed += 1
    if removed:
        log.info(
            "expired mirror run logs deleted",
            extra={"context": {"deleted": removed, "kept": keep}},
        )
    return removed


def render_run_log(report: MirrorReport, settings: Settings) -> str:
    """Render one run as the text a person opening the file weeks later would want.

    Args:
        report: The finished run.
        settings: The validated settings it used.

    Returns:
        The whole file, as plain ASCII text with Unix line endings.
    """
    repairing = report.mode is RunMode.REPAIR
    heading = (
        f"{report.jurisdiction_code} corpus {'repair' if repairing else 'mirror'} - "
        f"{iso_week(report.started_at)}"
    )
    contents = _contents_rows(report)
    blocks = [
        f"{heading}\n{'=' * len(heading)}\n\n{_verdict(report)}",
        _section("The run", _run_rows(report)),
        _section(
            "What it compared" if repairing else "The window it covered", _window_rows(report)
        ),
        _section("What it did", _work_rows(report)),
        _section("What the store holds now", contents) if contents else "",
        _section("Traffic to the source", _traffic_rows(report, settings)),
        _failures(report),
        _section("Where the rest of the record is", _neighbours(report)),
    ]
    return "\n\n".join(block for block in blocks if block) + "\n"


def _missing(report: MirrorReport) -> int:
    """Return how many listed cases a repair found the store did not hold.

    Args:
        report: The run.

    Returns:
        The identifiers listed, less the ones already on disk. Derived rather than counted,
        so it cannot disagree with the two numbers printed beside it.
    """
    return max(report.counters.discovered - report.counters.skipped, 0)


def _verdict(report: MirrorReport) -> str:
    """Summarise the whole run in one sentence, for a reader who reads no further.

    Args:
        report: The finished run.

    Returns:
        The sentence.
    """
    counters = report.counters
    got = _count(counters.mirrored, "new case") if counters.mirrored else "nothing new"
    failed = f", {counters.errors:,} failed" if counters.errors else ""
    if report.status is IngestStatus.FAILED:
        return f"FAILED after {got}{failed}: {report.error_message or 'no reason was recorded'}"
    if report.status is IngestStatus.INTERRUPTED:
        return f"INTERRUPTED after {got}{failed}. Nothing is lost; the next run resumes."
    if report.mode is RunMode.REPAIR:
        return _repair_verdict(report)
    if not counters.discovered:
        return "Nothing at all was offered in this window. Read the window below before the counts."
    if not counters.mirrored and not counters.errors:
        return (
            f"A quiet week: {_count(counters.skipped, 'case')} offered, all of them already "
            "held, nothing new to fetch."
        )
    return f"{got.capitalize()}{failed}."


def _repair_verdict(report: MirrorReport) -> str:
    """Summarise a repair, whose counts mean something else.

    Args:
        report: The finished repair.

    Returns:
        The sentence. A repair is judged on the gap it closed rather than on what was new at
        the source, so the number to lead with is how much of the difference came down.
    """
    counters = report.counters
    if not counters.discovered:
        return (
            "The source listed no identifiers at all, so nothing could be compared. Read the "
            "listing below before the counts."
        )
    missing = _missing(report)
    if not missing:
        return (
            f"Nothing to repair: {_count(counters.discovered, 'identifier')} listed, and the "
            "store already held every one."
        )
    failed = f", {counters.errors:,} failed" if counters.errors else ""
    return (
        f"{counters.mirrored:,} of {_count(missing, 'missing case')} fetched{failed}, out of "
        f"{_count(counters.discovered, 'identifier')} the source listed."
    )


def _run_rows(report: MirrorReport) -> list[tuple[str, str]]:
    """Return the when, the how long and the outcome.

    Args:
        report: The finished run.

    Returns:
        Label and value pairs, in reading order.
    """
    rows = [
        ("Started", report.started_at.astimezone(UTC).strftime(_INSTANT)),
        (
            "Finished",
            report.finished_at.astimezone(UTC).strftime(_INSTANT)
            if report.finished_at
            else "not recorded - the process did not reach the end of the run",
        ),
        ("Took", _duration(_seconds(report))),
        ("Outcome", _outcome(report)),
    ]
    if report.mode is RunMode.REPAIR:
        rows.append(
            (
                "Mode",
                "repair - the source's identifiers were listed and compared with the store, "
                "and only the cases missing from it were fetched. The discovery walk was not "
                "run, and the capture's own resume position was not touched",
            )
        )
    if report.error_message:
        rows.append(("Reason", report.error_message))
    if report.limit is not None:
        rows.append(("Limit", f"--limit {report.limit:,} was in force; this was a rehearsal"))
    rows += [
        ("Jurisdiction", report.jurisdiction_code),
        ("Connector", report.connector),
        ("Store", str(report.store_path)),
    ]
    return rows


def _outcome(report: MirrorReport) -> str:
    """Explain the terminal state in the vocabulary of the run that reached it.

    Args:
        report: The finished run.

    Returns:
        The sentence. A repair borrows the capture's wording wherever the two mean the same
        thing — an interruption does — and overrides it where they do not.
    """
    if report.mode is RunMode.REPAIR:
        return _REPAIR_OUTCOMES.get(report.status, _OUTCOMES[report.status])
    return _OUTCOMES[report.status]


def _window_rows(report: MirrorReport) -> list[tuple[str, str]]:
    """Return what the run asked the source for, and where it left the position.

    Args:
        report: The finished run.

    Returns:
        Label and value pairs. The pair of checkpoints is the part that says how much of the
        source the run actually looked at, and whether the window moved at all.
    """
    if report.mode is RunMode.REPAIR:
        return _repair_rows(report)
    since = report.window_since
    before = _position(report.checkpoint_before)
    after = _position(report.checkpoint_after)
    rows = [
        (
            "Asked for",
            f"cases modified from {since.isoformat()} onwards"
            if since
            else "everything the source holds - no lower bound was given or stored",
        ),
        (
            "Up to",
            report.window_until.isoformat()
            if report.window_until
            else "whatever the source held when the run enumerated it",
        ),
        ("Resumed from", before),
        ("Left at", after),
    ]
    if before == after:
        rows.append(("Note", "the position did not move: nothing new was successfully stored"))
    return rows


def _repair_rows(report: MirrorReport) -> list[tuple[str, str]]:
    """Return what a repair listed, and how far through the listing it got.

    Args:
        report: The finished repair.

    Returns:
        Label and value pairs. A repair has no window in the capture's sense: it is not
        resumed from a stored instant, and the position it records is a place in an
        identifier listing rather than a high-water mark, so the rows say which of the two
        this reader is looking at.
    """
    since, until = report.window_since, report.window_until
    listed = (
        f"identifiers modified from {since.isoformat()} onwards"
        if since
        else "every identifier the source lists"
    )
    rows = [
        ("Listed", listed),
        (
            "Up to",
            until.isoformat() if until else "the end of the listing, with no upper bound given",
        ),
        (
            "Compared with",
            "the case folders on disk - a case whose metadata.json is present was skipped "
            "without a request",
        ),
        ("Got as far as", _listing_position(report.checkpoint_after)),
        (
            "Note",
            "a repair reads and writes _repair_checkpoint.json and never _checkpoint.json, so "
            "the capture resumes exactly where it did before this run",
        ),
    ]
    return rows


def _listing_position(checkpoint: Checkpoint | None) -> str:
    """Render how far through an identifier listing a repair got.

    Args:
        checkpoint: The repair's position, or ``None``.

    Returns:
        The last identifier compared, or a phrase saying none was. Deliberately not
        :func:`_position`: that one leads with an instant, and a listing has none.
    """
    if checkpoint is None or checkpoint.last_source_id is None:
        return "no identifier was compared"
    return (
        f"{checkpoint.last_source_id} - what makes the next repair resume is the store itself, "
        "not this position: a case on disk is skipped locally, so the difference is derived "
        "again rather than remembered"
    )


def _position(checkpoint: Checkpoint | None) -> str:
    """Render a checkpoint as a sentence rather than as a record.

    Args:
        checkpoint: The stored position, or ``None``.

    Returns:
        The instant and the case it sits on, or a phrase saying there was none.
    """
    nothing = "nothing stored - this was the first run against this store"
    if checkpoint is None:
        return nothing
    seen = checkpoint.last_modified_seen
    case = checkpoint.last_source_id
    if seen is None and case is None:
        return nothing
    instant = seen.isoformat() if seen is not None else "an unrecorded instant"
    return f"{instant}, after case {case}" if case else instant


def _work_rows(report: MirrorReport) -> list[tuple[str, str]]:
    """Return the counts, each with what it means.

    Args:
        report: The finished run.

    Returns:
        Label and value pairs. ``Already held`` is kept beside ``Newly mirrored`` on purpose:
        the two together are what separate a quiet week from a run that did nothing. A repair
        reads the same counters differently — what it offered is a listing rather than a
        window, and the gap it set out to close is the difference between the two — so it
        gains a ``Missing`` row and says ``Listed`` where a capture says ``Discovered``.
    """
    counters = report.counters
    listed = (
        ("Listed", f"{_count(counters.discovered, 'identifier')} the source holds")
        if report.mode is RunMode.REPAIR
        else ("Discovered", f"{_count(counters.discovered, 'case')} offered in that window")
    )
    rows = [
        listed,
        (
            "Already held",
            f"{counters.skipped:,} skipped without a single request, the store had them",
        ),
    ]
    if report.mode is RunMode.REPAIR:
        rows.append(("Missing", f"{_missing(report):,} the store did not hold"))
    rows += [
        ("Newly mirrored", f"{counters.mirrored:,} fetched and written"),
        ("Failed", f"{counters.errors:,} - see below" if counters.errors else "0"),
        ("Written", _size(counters.bytes_written)),
    ]
    if report.cases_on_disk is not None:
        rows.append(("Store now holds", f"{_count(report.cases_on_disk, 'case')} on disk"))
    return rows


def _contents_rows(report: MirrorReport) -> list[tuple[str, str]]:
    """Return what the store was found to contain, counted from the store.

    This is the same observation the manifest's ``contents`` block records
    (``docs/architecture.md`` rule 2.11), printed here so that the figure an operator reads on
    a Monday morning and the figure a methodology page cites are one figure. It is deliberately
    not derived from this run's counters: those describe the run.

    Args:
        report: The finished run.

    Returns:
        Label and value pairs, or an empty list when the run ended before the store could be
        walked — a failed run's log says what it managed, and inventing a description of the
        corpus from its settings is the mistake this section exists to retire.
    """
    survey = report.survey
    if survey is None:
        return []
    walked = survey.observed_at.astimezone(UTC).strftime(_INSTANT)
    rows = [("Counted", f"{survey.cases:,} cases on disk, walked at {walked}")]
    if survey.resource_types:
        rows.append(("Resource types", _breakdown(survey.resource_types, "kinds of record")))
    if survey.languages:
        rows.append(("Text held in", _breakdown(survey.languages, "languages")))
    if survey.earliest_decision and survey.latest_decision:
        rows.append(("Decisions from", f"{survey.earliest_decision} to {survey.latest_decision}"))
    if survey.first_fetched_at and survey.last_fetched_at:
        rows.append(("Fetched between", f"{survey.first_fetched_at} and {survey.last_fetched_at}"))
    rows.append(
        ("Payloads", f"{_size(survey.payload_bytes)}, as the cases' own records state them")
    )
    if survey.unreadable:
        rows.append(
            (
                "Unreadable",
                f"{survey.unreadable:,} case folders hold metadata that could not be parsed; "
                "they are counted above but described by nothing else here",
            )
        )
    return rows


def _breakdown(counts: Mapping[str, int], noun: str) -> str:
    """Render one observed breakdown on a line.

    Args:
        counts: The values and their counts, largest first.
        noun: What the values are, for the count that leads the line.

    Returns:
        ``17 kinds of record - INFO_JUDICIAL 48,854; JUDG 25,823; ...``. How many there are
        comes first, because that is the number a reader compares against what they expected
        the corpus to be.
    """
    listed = "; ".join(f"{name} {count:,}" for name, count in counts.items())
    return f"{len(counts)} {noun} - {listed}"


def _traffic_rows(report: MirrorReport, settings: Settings) -> list[tuple[str, str]]:
    """Return what the run asked of the source, and how hard the source pushed back.

    Args:
        report: The finished run.
        settings: The settings it ran under, for the configured rate.

    Returns:
        Label and value pairs. These are the evidence for ``docs/architecture.md`` rule 2.5:
        a source that asked us to slow down, and whether we did.
    """
    configured = f"{settings.http_requests_per_second:g} requests/second configured"
    traffic = report.traffic
    if traffic is None:
        return [("Requests", "not reported by this connector"), ("Rate", configured)]
    seconds = _seconds(report)
    measured = f"; {traffic.requests / seconds:.2f}/s actually sent" if seconds > 0 else ""
    return [
        (
            "Requests",
            f"{_count(traffic.requests, 'request')} sent, {traffic.retries:,} of them retries",
        ),
        (
            "Retry-After",
            f"{_count(traffic.retry_after_waits, 'wait')} honoured as asked, "
            f"{_duration(traffic.retry_after_seconds)} in total"
            if traffic.retry_after_waits
            else "none - the source never asked us to wait",
        ),
        ("Backing off", f"{_duration(traffic.backoff_seconds)} in total"),
        ("Rate", f"{configured}{measured}"),
    ]


def _failures(report: MirrorReport) -> str:
    """Return the failures section: what failed, why, and where the full record is.

    Args:
        report: The finished run.

    Returns:
        The rendered section, or an empty string when nothing failed - a clean run should not
        have to be read past a heading that says so twice.
    """
    if not report.counters.errors:
        return ""
    lines = [
        _fill(
            f"{_count(report.counters.errors, 'case')} did not come down. The full record of "
            "them, this run's and every earlier run's, is in _failures.jsonl one directory up."
        ),
        "",
    ]
    lines += [f"  {count:>6,}  {kind}" for kind, count in report.failure_kinds.most_common()]
    listed = report.failures[:_FAILURES_LISTED]
    unlisted = report.counters.errors - len(listed)
    if listed:
        lines += ["", "  The first of them:" if unlisted else "  Which cases:"]
        lines += [
            _fill(
                f"{failure.source_id}  {failure.reason}",
                initial_indent="    ",
                subsequent_indent="      ",
            )
            for failure in listed
        ]
    if unlisted > 0:
        lines.append(f"    ... and {unlisted:,} more, in _failures.jsonl")
    lines += [
        "",
        "  A failed case holds the checkpoint at the case before it, so the window stays open",
        "  and the next run offers it again. One that fails every week is worth looking at.",
    ]
    return _section("Failures", lines)


def _neighbours(report: MirrorReport) -> list[tuple[str, str]]:
    """Return the guide to the other files in the store.

    Args:
        report: The finished run, which decides whether the repair's own position file is
            worth naming — a store no repair has run against does not hold one.

    Returns:
        Label and value pairs naming each file and what it is for, so a reader who has never
        seen this store can find the rest without reading any code.
    """
    rows = [
        (
            "manifest.json",
            "what this corpus is: contents counted from the store, the capture window, and "
            "the configuration the last completed run was launched with",
        ),
        ("_checkpoint.json", "where the next capture resumes"),
    ]
    if report.mode is RunMode.REPAIR:
        rows.append(("_repair_checkpoint.json", "how far the last repair read the listing"))
    rows += [
        ("_failures.jsonl", "every case that has ever failed, one JSON object per line"),
        (f"{LOG_DIR_NAME}/", "one file per run, this one included, oldest name first"),
        ("<case>/", "one folder per case: metadata.json, and the payloads verbatim"),
    ]
    return rows


def _section(title: str, rows: Sequence[tuple[str, str] | str]) -> str:
    """Render one titled block.

    Args:
        title: The heading.
        rows: Either label and value pairs, which are aligned into two columns, or ready-made
            lines, which are emitted as they are.

    Returns:
        The block, without a trailing newline.
    """
    labels = [row[0] for row in rows if isinstance(row, tuple)]
    width = max([_LABEL_WIDTH, *(len(label) for label in labels)])
    body = [
        _fill(
            row[1],
            initial_indent=f"{row[0].ljust(width)}  ",
            subsequent_indent=" " * (width + 2),
        )
        if isinstance(row, tuple)
        else row
        for row in rows
    ]
    return "\n".join([title, "-" * len(title), *body])


def _seconds(report: MirrorReport) -> float:
    """Return how long the run took.

    Args:
        report: The run.

    Returns:
        Seconds between start and finish, or ``0.0`` when it never recorded a finish.
    """
    if report.finished_at is None:
        return 0.0
    return max((report.finished_at - report.started_at).total_seconds(), 0.0)


def _fill(text: str, *, initial_indent: str = "", subsequent_indent: str = "") -> str:
    """Wrap one paragraph to the page width.

    Args:
        text: The paragraph.
        initial_indent: Prefix of the first line, e.g. the label column.
        subsequent_indent: Prefix of every line after it.

    Returns:
        The wrapped text. A long word is never broken and a hyphen is never wrapped at, so a
        store path or a URL stays on one line and can be copied out of the file whole - which
        is worth more than a straight right margin.
    """
    return textwrap.fill(
        text,
        width=_WIDTH,
        initial_indent=initial_indent,
        subsequent_indent=subsequent_indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def _count(number: int, noun: str) -> str:
    """Render a count and its noun, agreeing in number.

    Args:
        number: How many.
        noun: The singular noun; the plural is it with an ``s``.

    Returns:
        e.g. ``1 case``, ``18,204 cases``. Written out rather than left as ``case(s)``,
        because this file is read by a person and not filled in by one.
    """
    return f"{number:,} {noun}" if number == 1 else f"{number:,} {noun}s"


def _size(byte_count: int) -> str:
    """Render a quantity of bytes at a scale a reader can hold in their head.

    Args:
        byte_count: The bytes written.

    Returns:
        e.g. ``12.4 MB``, ``840.2 kB``, ``0 bytes``. A quiet week that wrote forty kilobytes
        should not be reported as ``0.0 MB``, which reads as nothing at all.
    """
    if byte_count >= 1_000_000:
        return f"{byte_count / 1_000_000:.1f} MB"
    if byte_count >= 1_000:
        return f"{byte_count / 1_000:.1f} kB"
    return _count(byte_count, "byte")


def _duration(seconds: float) -> str:
    """Render a span of time the way a person would say it.

    Args:
        seconds: The span.

    Returns:
        e.g. ``36m 26s``, ``4h 02m 11s``, ``0s``. Never a bare float: the reader is deciding
        whether a run took longer than usual, not doing arithmetic.
    """
    whole = round(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, remainder = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {remainder:02d}s"
    if minutes:
        return f"{minutes}m {remainder:02d}s"
    return f"{remainder}s"
