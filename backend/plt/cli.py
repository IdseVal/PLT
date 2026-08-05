"""Command line entry points.

Two equivalent routes into the same commands, per ``docs/architecture.md`` sections 1 and 7:

* ``flask --app plt.app plt <command>`` - the group is attached to the Flask CLI;
* ``python -m plt.cli <command>`` / ``plt <command>`` - the same group standalone, so a
  server cron can call the identical code path as the scheduled workflow.

Five commands live here. ``plt ingest`` scans a jurisdiction, ``plt mirror`` copies a
jurisdiction's corpus to disk verbatim so a selection experiment can be repeated over an
identical corpus, ``plt digest`` sends the weekly mailing-list digest,
``plt purge-subscribers`` applies whatever retention rules a deployment has configured for
that list, and ``plt seed-vocabularies`` refreshes the court tables. All of them are what the
scheduled workflows call, so anything reproducible in a terminal is what runs unattended.

``plt ingest`` is that path. It is a thin shell around
:func:`plt.pipeline.runner.run_jurisdiction`: parse the window, pick the jurisdictions, print
the counts, and translate the outcome into an exit code the scheduler can act on — ``0`` for
a run that completed, ``1`` for one that failed, ``2`` for a usage error (click's own), ``3``
for a run that completed only partly and was asked to say so, and ``130`` for one that was
interrupted.

**Why ``3`` exists.** A ``partial`` run is one where documents failed: the checkpoint stops
advancing at the first failure, so the window stays open and the next run retries from there.
That is the right behaviour for an operator watching the output, and it is 0 by default
because the run did complete. It is the wrong answer for a weekly unattended job: a
jurisdiction whose window has been frozen for a month reports green every Monday while the
tracker quietly stops gaining cases, which is precisely the failure
``docs/core-document.md`` section 2.7 says is the expensive one — nobody can audit an
absence. ``--fail-on-partial`` makes that visible without changing the exit code any existing
caller sees, and the scheduled workflow passes it.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import click
from sqlalchemy.orm import Session, sessionmaker

from plt.config import Settings, get_settings
from plt.db.models import IngestStatus
from plt.db.session import get_session_factory, session_scope
from plt.notifications.digest import run_digest
from plt.notifications.mailer import MailError, build_mailer
from plt.notifications.retention import run_purge
from plt.notifications.reviews import notify_new_reviews
from plt.pipeline.base import PipelineError
from plt.pipeline.mirror import MirrorReport, mirror_jurisdiction
from plt.pipeline.persistence import resolve_court
from plt.pipeline.registry import available_jurisdictions, connector_classes, connector_for
from plt.pipeline.runner import IngestReport, run_jurisdiction
from plt.utils.logging import configure_logging, get_logger

__all__ = ["main", "plt_cli"]

log = get_logger(__name__)

#: Exit code for a run stopped by ``Ctrl+C``; the shell convention of 128 + SIGINT.
_EXIT_INTERRUPTED = 130

#: Exit code for a run that completed with failed documents, under ``--fail-on-partial``.
#: Not ``2``: click already spends that on a usage error, and a scheduler must be able to
#: tell "your command line is wrong" from "the window did not advance".
_EXIT_PARTIAL = 3


def _parse_timestamp(
    ctx: click.Context,
    param: click.Parameter,
    value: Any,  # noqa: ANN401 - click hands over whatever the shell gave
) -> datetime | None:
    """Parse an ISO 8601 date or timestamp from the command line, as UTC.

    A naive value is read as UTC rather than as local time: the checkpoints, the schema and
    the source windows are all UTC, and silently applying the operator's local offset would
    shift a window by an hour twice a year.

    Args:
        ctx: The click context, unused.
        param: The parameter being parsed, named in the error message.
        value: The raw value, or ``None`` when the option was not given.

    Returns:
        A timezone-aware UTC timestamp, or ``None``.

    Raises:
        click.BadParameter: If the value is not an ISO 8601 date or timestamp.
    """
    del ctx
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as error:
        message = f"{value!r} is not an ISO 8601 date or timestamp, e.g. 2026-01-31 or 2026-01-31T09:00:00Z"  # noqa: E501
        raise click.BadParameter(message, param=param) from error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@click.group(name="plt")
@click.version_option(package_name="plt")
def plt_cli() -> None:
    """PLT maintenance and ingestion commands."""
    configure_logging(get_settings())


@plt_cli.command(name="ingest")
@click.option(
    "--jurisdiction",
    "-j",
    "jurisdictions",
    multiple=True,
    metavar="CODE",
    help="Jurisdiction to ingest, e.g. NL. Repeat for several.",
)
@click.option(
    "--all",
    "every_jurisdiction",
    is_flag=True,
    help="Ingest every jurisdiction a connector exists for.",
)
@click.option(
    "--since",
    metavar="TIMESTAMP",
    callback=_parse_timestamp,
    default=None,
    help="Only documents modified since this ISO 8601 instant, UTC. Defaults to the checkpoint.",
)
@click.option(
    "--until",
    metavar="TIMESTAMP",
    callback=_parse_timestamp,
    default=None,
    help="Only documents modified up to this ISO 8601 instant, UTC.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Run every stage and write a match report, without touching the database.",
)
@click.option(
    "--report",
    "report_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the match report here instead of the configured report directory.",
)
@click.option(
    "--batch-size",
    type=click.IntRange(1, 1000),
    default=None,
    help="Documents processed and committed per batch. Defaults to PLT_PIPELINE_BATCH_SIZE.",
)
@click.option(
    "--fail-on-partial",
    is_flag=True,
    help="Exit 3 when a run completed but documents failed, so an unattended job goes red "
    "instead of reporting success over a window that stopped advancing.",
)
@click.option(
    "--no-notify",
    is_flag=True,
    help="Do not email PLT_ADMIN_EMAIL about the review items this scan flagged.",
)
def ingest(
    jurisdictions: tuple[str, ...],
    every_jurisdiction: bool,
    since: datetime | None,
    until: datetime | None,
    dry_run: bool,
    report_path: Path | None,
    batch_size: int | None,
    fail_on_partial: bool,
    no_notify: bool,
) -> None:
    """Fetch, filter and store one or more jurisdictions' case law.

    Run without ``--since`` — as the weekly job does — the stored checkpoint supplies the
    window, so each run picks up where the last one left off.

    When the scan flags cases for review and ``PLT_ADMIN_EMAIL`` is set, one message goes out
    afterwards listing them (core document 2.7): the queue is not public, so without this
    nothing would tell anybody it had gained items. It is sent here rather than inside the
    runner so that the scheduled workflow, a server cron and this terminal all get it from the
    one command, and a library caller of ``run_jurisdiction`` gets none.

    Raises:
        click.UsageError: If no jurisdiction was named, or ``--report`` was combined with
            several jurisdictions, which would have them overwrite each other's report.
        click.ClickException: If any run failed; the message names the jurisdictions.
    """
    settings = get_settings()
    codes = _selected_jurisdictions(jurisdictions, every_jurisdiction=every_jurisdiction)
    if report_path is not None and len(codes) > 1:
        message = "--report names one file, so it cannot be combined with several jurisdictions"
        raise click.UsageError(message)

    reports: list[IngestReport] = []
    for code in codes:
        report = run_jurisdiction(
            code,
            since,
            until,
            dry_run,
            settings=settings,
            batch_size=batch_size,
            report_path=report_path,
        )
        reports.append(report)
        click.echo(report.summary())
        if report.report_path is not None:
            click.echo(f"  match report: {report.report_path}")
        if report.status is IngestStatus.INTERRUPTED:
            # Do not start the next jurisdiction after Ctrl+C.
            break

    if not dry_run and not no_notify:
        _notify_reviews(reports, settings)

    _exit_for(reports, strict=fail_on_partial)


def _notify_reviews(reports: list[IngestReport], settings: Settings) -> None:
    """Tell the administrator about the review items these runs flagged.

    A failure to send is reported and swallowed: the scan itself succeeded, the flags are in
    the database, and turning a mail-server outage into a red weekly ingestion job would put
    the alarm on the wrong thing entirely.

    Args:
        reports: The runs that just finished.
        settings: Validated settings supplying the administrator's address.
    """
    if not reports:
        return
    since = min(report.started_at for report in reports)
    mailer = build_mailer(settings)
    try:
        with session_scope(get_session_factory()) as session:
            flagged = notify_new_reviews(session, settings, mailer, since=since)
    except MailError as error:
        click.echo(f"the review queue notice could not be sent: {error}", err=True)
        log.warning(
            "review queue notice could not be sent", extra={"context": {"error": str(error)}}
        )
        return
    finally:
        mailer.close()
    if flagged:
        click.echo(f"  review queue: {flagged} new item(s); notice sent to the configured admin")


@plt_cli.command(name="mirror")
@click.option(
    "--jurisdiction",
    "-j",
    "jurisdictions",
    multiple=True,
    metavar="CODE",
    help="Jurisdiction to mirror, e.g. EU. Repeat for several.",
)
@click.option(
    "--all",
    "every_jurisdiction",
    is_flag=True,
    help="Mirror every jurisdiction a connector exists for.",
)
@click.option(
    "--since",
    metavar="TIMESTAMP",
    callback=_parse_timestamp,
    default=None,
    help="Start of the window, ISO 8601 UTC. Defaults to the store's own checkpoint, which "
    "is what makes an interrupted capture resume rather than start over.",
)
@click.option(
    "--until",
    metavar="TIMESTAMP",
    callback=_parse_timestamp,
    default=None,
    help="End of the window, ISO 8601 UTC. Pin it for a capture that will take days: it is "
    "what gives the corpus a stated edge, and it is recorded in the manifest as such.",
)
@click.option(
    "--store",
    "store_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Root of the case-law store. Defaults to PLT_CORPUS_STORE_DIR.",
)
@click.option(
    "--limit",
    type=click.IntRange(1),
    default=None,
    help="Stop after this many cases have been fetched. For rehearsing a capture.",
)
@click.option(
    "--fail-on-partial",
    is_flag=True,
    help="Exit 3 when a run completed but cases failed, so an unattended job goes red "
    "instead of reporting success over a window that stopped advancing.",
)
def mirror(
    jurisdictions: tuple[str, ...],
    every_jurisdiction: bool,
    since: datetime | None,
    until: datetime | None,
    store_root: Path | None,
    limit: int | None,
    fail_on_partial: bool,
) -> None:
    """Mirror a jurisdiction's corpus to disk, verbatim, resuming where the last run stopped.

    This is not an ingestion: nothing is filtered, classified or written to the database. It
    stores what the source served, so that a selection experiment can be re-run over an
    identical corpus and two keyword lists compared on their merits rather than on the day
    they happened to run (core document 2.8).

    A case already on disk costs no request, so an interrupted capture is resumed by running
    the same command again. ``Ctrl+C`` finishes the case in flight, writes the position and
    exits 130.

    Raises:
        click.UsageError: If no jurisdiction was named.
        click.ClickException: If any run failed, if no connector serves a named jurisdiction,
            or if the store itself cannot be written to. The last of those is reported rather
            than raised through: a full or read-only disk is an operator's problem to read in
            one line, not a traceback.
    """
    settings = get_settings()
    codes = _selected_jurisdictions(jurisdictions, every_jurisdiction=every_jurisdiction)
    reports: list[MirrorReport] = []
    for code in codes:
        try:
            report = mirror_jurisdiction(
                code,
                since,
                until,
                settings=settings,
                store_root=store_root,
                limit=limit,
            )
        except PipelineError as error:
            raise click.ClickException(f"{code}: {error}") from error
        reports.append(report)
        click.echo(report.summary())
        if report.status is IngestStatus.INTERRUPTED:
            # Do not start the next jurisdiction after Ctrl+C.
            break
    _exit_for(reports, strict=fail_on_partial)


@plt_cli.command(name="digest")
@click.option(
    "--since",
    metavar="TIMESTAMP",
    callback=_parse_timestamp,
    default=None,
    help="Start of the window, ISO 8601 UTC. Defaults to PLT_DIGEST_PERIOD_DAYS before the end.",
)
@click.option(
    "--until",
    metavar="TIMESTAMP",
    callback=_parse_timestamp,
    default=None,
    help="End of the window, ISO 8601 UTC. Defaults to now. The window identifies the send: "
    "repeating a run with the same --until reaches only the recipients it did not reach "
    "before, so pass it when resuming an interrupted digest.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Render every message and send none, leaving the database untouched.",
)
def digest(since: datetime | None, until: datetime | None, dry_run: bool) -> None:
    """Send the weekly digest of newly found cases to the confirmed subscribers.

    The window is over ``case.first_seen_at`` — what is new *to the tracker* — so a judgment
    from 2019 that the pipeline reached this week is news and one delivered yesterday that has
    been stored for a month is not.

    Only confirmed addresses are written to, the send is batched and resumable, and a quiet
    week sends nothing at all rather than an empty message. What actually leaves the process
    is decided by ``PLT_MAIL_BACKEND``, which is the console backend unless the deployment
    said otherwise: this command cannot mail a real person by accident.

    Raises:
        click.ClickException: If the window is empty or inverted.
        click.exceptions.Exit: With 130 when a signal ended the send, so a scheduler can tell
            a cancellation from a failure.
    """
    settings = get_settings()
    try:
        report = run_digest(since=since, until=until, dry_run=dry_run, settings=settings)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    except MailError as error:
        raise click.ClickException(f"the digest could not be sent: {error}") from error

    click.echo(report.summary())
    if report.interrupted:
        raise click.exceptions.Exit(_EXIT_INTERRUPTED)


@plt_cli.command(name="purge-subscribers")
def purge_subscribers() -> None:
    """Apply the mailing list's retention rules, if a deployment has configured any.

    Two rules, neither of them decided here (issue #75, core document section 2.12): drop the
    digest from an unsubscribed row once ``PLT_SUBSCRIBER_RETENTION_DAYS`` has passed, and
    delete an address that never confirmed once ``PLT_SUBSCRIBER_UNCONFIRMED_EXPIRY_DAYS``
    has. An unset period means the rule is **not enforced**, and the output says so rather
    than reporting a count of zero — the two look the same and mean opposite things.

    Belongs beside the weekly digest in whatever schedule runs this deployment.

    Raises:
        click.exceptions.Exit: With 130 when a signal ended the purge, so a scheduler can tell
            a cancellation from a failure. What was committed stays committed.
    """
    report = run_purge(settings=get_settings())
    click.echo(report.summary())
    if report.interrupted:
        raise click.exceptions.Exit(_EXIT_INTERRUPTED)


@plt_cli.command(name="jurisdictions")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a JSON array of codes, which is what the weekly workflow builds its matrix from.",
)
def jurisdictions(as_json: bool) -> None:
    """List the jurisdictions ``plt ingest --all`` would run.

    The list comes from the connector registry, so onboarding a jurisdiction stays one
    connector plus one keyword list (``docs/architecture.md`` section 4) and no scheduler,
    crontab or workflow file has to be edited to include it.

    Raises:
        click.ClickException: If no connector is registered. A caller building a schedule
            from an empty list would silently ingest nothing, so this is reported rather
            than printed as an empty result.
    """
    registered = connector_classes()
    if not registered:
        message = (
            "no connectors are registered; add one under plt.pipeline.connectors together "
            "with its keyword list under data/keywords/"
        )
        raise click.ClickException(message)
    codes = sorted(registered)
    if as_json:
        click.echo(json.dumps(codes))
        return
    for code in codes:
        click.echo(f"{code}\t{registered[code].name}")


@plt_cli.command(name="seed-vocabularies")
@click.option(
    "--jurisdiction",
    "-j",
    "jurisdictions",
    multiple=True,
    metavar="CODE",
    help="Jurisdiction whose reference tables to seed, e.g. NL. Repeat for several.",
)
@click.option(
    "--all",
    "every_jurisdiction",
    is_flag=True,
    help="Seed every jurisdiction a connector exists for.",
)
def seed_vocabularies(jurisdictions: tuple[str, ...], every_jurisdiction: bool) -> None:
    """Seed the court table from each source's controlled vocabulary.

    Courts are matched on ``(jurisdiction_code, source_identifier)`` — the source's own
    vocabulary URI — so running this again updates the rows it created rather than adding a
    second set. It is therefore safe to re-run after the vocabulary changes, and safe to run
    before every ingestion.

    Raises:
        click.UsageError: If no jurisdiction was named.
        click.ClickException: If a source's vocabulary could not be read.
    """
    settings = get_settings()
    codes = _selected_jurisdictions(jurisdictions, every_jurisdiction=every_jurisdiction)
    factory = get_session_factory()
    failures: list[str] = []
    for code in codes:
        try:
            seeded = _seed_courts(code, settings, factory)
        except PipelineError as error:
            failures.append(f"{code}: {type(error).__name__}: {error}")
            click.echo(f"{code}: failed; {error}")
            continue
        click.echo(f"{code}: {seeded} court(s) seeded from the source vocabulary")
    if failures:
        raise click.ClickException("; ".join(failures))


def _seed_courts(
    code: str,
    settings: Settings,
    factory: sessionmaker[Session],
) -> int:
    """Upsert one jurisdiction's courts from its connector's vocabulary.

    Args:
        code: Jurisdiction code.
        settings: Validated settings.
        factory: Session factory the upserts run in.

    Returns:
        The number of courts the vocabulary listed.

    Raises:
        PipelineError: If no connector serves the jurisdiction, or its vocabulary is
            unreadable.
    """
    connector = connector_for(code, settings)
    try:
        with session_scope(factory) as session:
            return sum(
                1
                for court in connector.iter_courts()
                if resolve_court(session, connector.jurisdiction_code, court) is not None
            )
    finally:
        connector.close()


def _selected_jurisdictions(
    jurisdictions: tuple[str, ...],
    *,
    every_jurisdiction: bool,
) -> tuple[str, ...]:
    """Work out which jurisdictions to run.

    Args:
        jurisdictions: Codes named with ``--jurisdiction``.
        every_jurisdiction: Whether ``--all`` was given.

    Returns:
        The codes to run, upper-cased, in the order given (or sorted, for ``--all``).

    Raises:
        click.UsageError: If neither or both selectors were used, or no connector exists.
    """
    if every_jurisdiction and jurisdictions:
        message = "--all and --jurisdiction are mutually exclusive"
        raise click.UsageError(message)
    if every_jurisdiction:
        codes = available_jurisdictions()
        if not codes:
            message = (
                "no connectors are registered, so --all has nothing to run; add one under "
                "plt.pipeline.connectors"
            )
            raise click.UsageError(message)
        return codes
    if not jurisdictions:
        known = ", ".join(available_jurisdictions()) or "none registered yet"
        message = f"name a jurisdiction with --jurisdiction, or use --all. Known: {known}"
        raise click.UsageError(message)
    return tuple(code.strip().upper() for code in jurisdictions)


class _Counted(Protocol):
    """The one counter the exit-code policy reads off a run."""

    @property
    def errors(self) -> int:
        """Return how many documents or cases failed."""


class _RunOutcome(Protocol):
    """What :func:`_exit_for` needs of a run, whichever command produced it.

    Structural rather than nominal on purpose: ``plt ingest`` and ``plt mirror`` are different
    jobs over different stores, but "failed outranks interrupted outranks partial" is one
    policy and a scheduler reads it from one place.
    """

    @property
    def status(self) -> IngestStatus:
        """Return the run's terminal state."""

    @property
    def jurisdiction_code(self) -> str:
        """Return the jurisdiction the run covered."""

    @property
    def error_message(self) -> str | None:
        """Return why the run failed, when it did."""

    @property
    def counters(self) -> _Counted:
        """Return what the run did, counted."""


def _exit_for(reports: Sequence[_RunOutcome], *, strict: bool = False) -> None:
    """Translate the runs' outcomes into an exit code.

    The order is deliberate: a failure outranks an interruption, and an interruption outranks
    a partial run, so the worst outcome of the batch is the one the scheduler sees.

    Args:
        reports: One report per jurisdiction that ran, from ``plt ingest`` or ``plt mirror``.
        strict: Whether a ``partial`` run should exit non-zero. Off by default, which keeps
            the documented ``0``/``1``/``130`` contract for interactive use; the weekly job
            turns it on.

    Raises:
        click.exceptions.Exit: With 130 when a run was interrupted, so a scheduler can tell
            a cancellation from a failure; with 3 for a partial run under ``strict``.
        click.ClickException: If any run failed, which exits 1.
    """
    failed = [report for report in reports if report.status is IngestStatus.FAILED]
    if failed:
        detail = "; ".join(
            f"{report.jurisdiction_code}: {report.error_message or 'unknown error'}"
            for report in failed
        )
        raise click.ClickException(f"ingestion failed for {len(failed)} jurisdiction(s); {detail}")
    if any(report.status is IngestStatus.INTERRUPTED for report in reports):
        raise click.exceptions.Exit(_EXIT_INTERRUPTED)
    partial = [report for report in reports if report.status is IngestStatus.PARTIAL]
    if strict and partial:
        detail = ", ".join(
            f"{report.jurisdiction_code} ({report.counters.errors} document(s))"
            for report in partial
        )
        click.echo(
            f"partial: {detail}. The checkpoint stopped at the first failed document, so the "
            f"window will be retried rather than skipped.",
            err=True,
        )
        raise click.exceptions.Exit(_EXIT_PARTIAL)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv: Argument vector, excluding the program name. Defaults to ``sys.argv[1:]``.

    Returns:
        A process exit code: ``0`` on success, ``1`` on an unhandled failure, ``2`` on a
        usage error, ``3`` for a partial run under ``--fail-on-partial``, and ``130`` when
        interrupted with Ctrl+C.
    """
    try:
        result = plt_cli.main(args=argv, standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.exceptions.Abort:
        return _EXIT_INTERRUPTED
    except KeyboardInterrupt:
        log.warning("interrupted; shutting down cleanly")
        return _EXIT_INTERRUPTED
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(main())
