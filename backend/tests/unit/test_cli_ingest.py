"""``plt ingest``: the path the weekly workflow and a server cron both call.

The exit code is the contract with the scheduler, so it is tested through :func:`plt.cli.main`
rather than through click's runner: 0 for a run that completed, 1 for one that failed, 130 for
one that was interrupted. A scheduler that cannot tell a cancellation from a breakage will
page somebody at three in the morning for the wrong reason.
"""

from __future__ import annotations

import json
import signal
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from plt.cli import main
from plt.config import Settings
from plt.db.base import Base
from plt.db.models import Case, Jurisdiction, JurisdictionType
from plt.db.session import create_session_factory, make_engine
from plt.pipeline import registry, runner
from plt.pipeline.base import SourceUnavailableError
from tests.conftest import build_settings
from tests.fakes import PESTICIDE_TEXT, UNRELATED_TEXT, FakeConnector, documents

#: Documents the fake NL source publishes during these tests.
NL_DOCS = documents(3)


class CliConnector(FakeConnector):
    """The NL connector the CLI finds in the registry."""

    name = "cli-fake"

    def __init__(self, settings: Settings | None = None) -> None:
        """Publish a fixed set of documents."""
        super().__init__(settings, docs=NL_DOCS)


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    """Point the CLI at a temporary database and a registry holding the fake connector."""
    settings = build_settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'plt.db'}",
        pipeline_batch_size=2,
        pipeline_report_dir=tmp_path / "reports",
    )
    engine = make_engine(settings)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        session.add(Jurisdiction(code="NL", name="Netherlands", type=JurisdictionType.STATE))
        session.commit()

    monkeypatch.setattr("plt.cli.get_settings", lambda: settings)
    monkeypatch.setattr(runner, "get_session_factory", lambda: factory)
    # Seeding the registry rather than adding to it: ordinary registration runs discovery,
    # which finds the shipped connectors too, and ``--all`` would then drive them against
    # their live endpoints from a unit test.
    registry.reset_registry(CliConnector)
    try:
        yield factory
    finally:
        registry.reset_registry()
        engine.dispose()


def count_cases(factory: sessionmaker[Session]) -> int:
    """Count the stored cases."""
    with factory() as session:
        return session.execute(select(func.count()).select_from(Case)).scalar_one()


def test_ingesting_one_jurisdiction_stores_its_cases(
    cli_env: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["ingest", "--jurisdiction", "nl"])

    assert code == 0
    assert count_cases(cli_env) == len(NL_DOCS)
    assert "NL/cli-fake: success" in capsys.readouterr().out


def test_a_second_run_reports_that_it_inserted_nothing(
    cli_env: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    main(["ingest", "--jurisdiction", "NL"])
    capsys.readouterr()

    assert main(["ingest", "--jurisdiction", "NL", "--since", "2026-01-01"]) == 0

    assert count_cases(cli_env) == len(NL_DOCS)
    assert "inserted 0" in capsys.readouterr().out


def test_all_runs_every_registered_jurisdiction(
    cli_env: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    del cli_env

    assert main(["ingest", "--all"]) == 0

    assert "NL/cli-fake" in capsys.readouterr().out


def test_a_dry_run_writes_a_report_and_no_rows(
    cli_env: sessionmaker[Session], tmp_path: Path
) -> None:
    report = tmp_path / "match-report.jsonl"

    assert main(["ingest", "-j", "NL", "--dry-run", "--report", str(report)]) == 0

    assert count_cases(cli_env) == 0
    entries = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    assert entries[0]["type"] == "run"
    assert all(entry["passed"] for entry in entries[1:])


def test_an_interruption_exits_130(cli_env: sessionmaker[Session]) -> None:
    """Ctrl+C mid-run: the item in flight is finished and committed, then the process stops."""

    class Interrupting(FakeConnector):
        """A connector that raises SIGINT while the second document is being fetched."""

        name = "cli-interrupting"

        def __init__(self, settings: Settings | None = None) -> None:
            """Interrupt on the second document."""
            super().__init__(settings, docs=NL_DOCS, on_fetch=self._interrupt)

        def _interrupt(self, source_id: str) -> None:
            if source_id == NL_DOCS[1].source_id:
                signal.raise_signal(signal.SIGINT)

    registry.reset_registry(Interrupting)

    assert main(["ingest", "-j", "NL"]) == 130
    assert count_cases(cli_env) == 2


def test_a_failed_run_exits_1(cli_env: sessionmaker[Session]) -> None:
    del cli_env

    class Broken(FakeConnector):
        """A connector whose source is down."""

        name = "cli-broken"

        def __init__(self, settings: Settings | None = None) -> None:
            """Fail at discovery."""
            super().__init__(
                settings,
                docs=NL_DOCS,
                raise_on_discover=SourceUnavailableError("data.rechtspraak.nl is down"),
            )

    registry.reset_registry(Broken)

    assert main(["ingest", "-j", "NL"]) == 1


class Flaky(FakeConnector):
    """A connector whose source refuses to serve one of its documents."""

    name = "cli-flaky"

    def __init__(self, settings: Settings | None = None) -> None:
        """Fail on the second document, and serve the rest."""
        super().__init__(settings, docs=NL_DOCS, fail_fetch={NL_DOCS[1].source_id})


def test_a_partial_run_exits_0_by_default(cli_env: sessionmaker[Session]) -> None:
    """The documented contract for interactive use: the run completed, so the code is 0."""
    del cli_env
    registry.reset_registry(Flaky)

    assert main(["ingest", "-j", "NL"]) == 0


def test_a_partial_run_exits_3_when_asked_to_fail(
    cli_env: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    """What the weekly job runs: a frozen window has to be visible, not reported as success."""
    del cli_env
    registry.reset_registry(Flaky)

    assert main(["ingest", "-j", "NL", "--fail-on-partial"]) == 3

    captured = capsys.readouterr()
    assert "NL/cli-flaky: partial" in captured.out
    assert "partial: NL" in captured.err


def test_a_successful_run_still_exits_0_when_asked_to_fail_on_partial(
    cli_env: sessionmaker[Session],
) -> None:
    del cli_env

    assert main(["ingest", "-j", "NL", "--fail-on-partial"]) == 0


def test_a_failure_outranks_a_partial_run(cli_env: sessionmaker[Session]) -> None:
    """Two jurisdictions, one broken and one flaky: the scheduler is told about the failure."""
    with cli_env() as session:
        session.add(
            Jurisdiction(code="EU", name="European Union", type=JurisdictionType.SUPRANATIONAL)
        )
        session.commit()

    class Broken(FakeConnector):
        """An EU connector whose source is down."""

        jurisdiction_code = "EU"
        name = "cli-broken-eu"

        def __init__(self, settings: Settings | None = None) -> None:
            """Fail at discovery."""
            super().__init__(
                settings,
                docs=NL_DOCS,
                raise_on_discover=SourceUnavailableError("cellar.publications.europa.eu is down"),
            )

    registry.reset_registry(Flaky, Broken)

    assert main(["ingest", "-j", "NL", "-j", "EU", "--fail-on-partial"]) == 1


def test_naming_no_jurisdiction_is_a_usage_error(cli_env: sessionmaker[Session]) -> None:
    del cli_env

    assert main(["ingest"]) == 2


def test_all_and_jurisdiction_are_mutually_exclusive(cli_env: sessionmaker[Session]) -> None:
    del cli_env

    assert main(["ingest", "--all", "-j", "NL"]) == 2


def test_a_malformed_timestamp_is_rejected(cli_env: sessionmaker[Session]) -> None:
    del cli_env

    assert main(["ingest", "-j", "NL", "--since", "last tuesday"]) == 2


def test_a_report_path_cannot_be_shared_by_two_jurisdictions(
    cli_env: sessionmaker[Session], tmp_path: Path
) -> None:
    del cli_env

    exit_code = main(
        ["ingest", "-j", "NL", "-j", "EU", "--dry-run", "--report", str(tmp_path / "r.jsonl")]
    )

    assert exit_code == 2


def test_the_text_of_a_stored_case_is_the_one_that_was_filtered(
    cli_env: sessionmaker[Session],
) -> None:
    main(["ingest", "-j", "NL"])

    with cli_env() as session:
        stored = session.scalars(select(Case)).first()
        assert stored is not None
        assert stored.documents[0].full_text == PESTICIDE_TEXT
        assert UNRELATED_TEXT not in (stored.documents[0].full_text or "")
