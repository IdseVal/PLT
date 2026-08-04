"""Command line entry points.

Two equivalent routes into the same commands, per ``docs/architecture.md`` sections 1 and 7:

* ``flask --app plt.app plt <command>`` - the group is attached to the Flask CLI;
* ``python -m plt.cli <command>`` / ``plt <command>`` - the same group standalone, so a
  server cron can call the identical code path as the scheduled workflow.

``plt ingest`` is that path. It is a thin shell around
:func:`plt.pipeline.runner.run_jurisdiction`: parse the window, pick the jurisdictions, print
the counts, and translate the outcome into an exit code the scheduler can act on — ``0`` for
a run that completed, ``1`` for one that failed, ``130`` for one that was interrupted.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click
from sqlalchemy.orm import Session, sessionmaker

from plt.config import Settings, get_settings
from plt.db.models import IngestStatus
from plt.db.session import get_session_factory, session_scope
from plt.pipeline.base import PipelineError
from plt.pipeline.filters.keywords import KeywordListError
from plt.pipeline.persistence import resolve_court
from plt.pipeline.registry import available_jurisdictions, connector_for
from plt.pipeline.runner import IngestReport, run_jurisdiction
from plt.utils.logging import configure_logging, get_logger

__all__ = ["main", "plt_cli"]

log = get_logger(__name__)

#: Exit code for a run stopped by ``Ctrl+C``; the shell convention of 128 + SIGINT.
_EXIT_INTERRUPTED = 130


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
def ingest(
    jurisdictions: tuple[str, ...],
    every_jurisdiction: bool,
    since: datetime | None,
    until: datetime | None,
    dry_run: bool,
    report_path: Path | None,
    batch_size: int | None,
) -> None:
    """Fetch, filter and store one or more jurisdictions' case law.

    Run without ``--since`` — as the weekly job does — the stored checkpoint supplies the
    window, so each run picks up where the last one left off.

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

    _exit_for(reports)


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
        except (PipelineError, KeywordListError) as error:
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


def _exit_for(reports: list[IngestReport]) -> None:
    """Translate the runs' outcomes into an exit code.

    Args:
        reports: One report per jurisdiction that ran.

    Raises:
        click.exceptions.Exit: With 130 when a run was interrupted, so a scheduler can tell
            a cancellation from a failure.
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


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv: Argument vector, excluding the program name. Defaults to ``sys.argv[1:]``.

    Returns:
        A process exit code: ``0`` on success, ``130`` when interrupted with Ctrl+C,
        ``1`` on an unhandled failure.
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
