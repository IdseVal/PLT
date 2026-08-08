"""The record a mirror run leaves for a person: one file per run, in the store.

The mirror is about to run weekly with nobody watching, so what is under test here is not
that a function returns a string. It is that **a run always leaves a record** — including
the run that failed, which is the one the record exists for — that the record says the
things an operator has to know, and that nothing about it can cost a run its cases.

Everything drives a fake connector against a temporary directory. Nothing here writes to a
real store, and nothing touches the network (``CONTRIBUTING.md`` section 4).
"""

from __future__ import annotations

import signal
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from plt.config import Settings
from plt.db.models import IngestStatus
from plt.pipeline.base import SourceTraffic, SourceUnavailableError
from plt.pipeline.mirror import CorpusStore, CorpusStoreError, case_folder_name, mirror_jurisdiction
from plt.pipeline.runlog import (
    LOG_DIR_NAME,
    RUN_LOG_PATTERN,
    prune_run_logs,
    run_log_name,
)
from tests.conftest import build_settings
from tests.fakes import EPOCH, FakeConnector, FakeDocument, documents

# --------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------


class EuConnector(FakeConnector):
    """The fake source these runs mirror, under the EU code the store expects."""

    jurisdiction_code = "EU"
    name = "eurlex"


class TalkativeConnector(EuConnector):
    """A connector that reports its traffic, as one fetching through the polite client does."""

    def __init__(self, settings: Settings | None = None, **kwargs: object) -> None:
        """Build the fake and give it something to report.

        Args:
            settings: Settings, as the registry passes them.
            **kwargs: Passed through to :class:`~tests.fakes.FakeConnector`.
        """
        super().__init__(settings, **kwargs)  # type: ignore[arg-type]
        self.reported = SourceTraffic(
            requests=112, retries=3, retry_after_waits=1, retry_after_seconds=30.0
        )

    @property
    def traffic(self) -> SourceTraffic:
        """Return the counts this connector's client would have kept."""
        return self.reported


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return settings whose corpus store is a temporary directory of this test's own."""
    return build_settings(corpus_store_dir=tmp_path / "CaseLawStore", pipeline_batch_size=2)


def eu_documents(count: int = 3) -> list[FakeDocument]:
    """Build CELEX-shaped documents, one minute apart, oldest first.

    Args:
        count: How many to build.

    Returns:
        The documents, in the order discovery yields them.
    """
    return [
        FakeDocument(
            source_id=f"6202{index % 5}CJ{index:04d}",
            modified_at=document.modified_at,
            text=document.text,
            language="en",
        )
        for index, document in enumerate(documents(count))
    ]


def run(settings: Settings, docs: Sequence[FakeDocument], **overrides: object) -> None:
    """Mirror the fake source into the configured store.

    Args:
        settings: The test settings.
        docs: The documents the fake source holds.
        **overrides: Keyword arguments for the connector, e.g. ``fail_fetch``.
    """
    connector = EuConnector(settings, docs=docs, **overrides)  # type: ignore[arg-type]
    mirror_jurisdiction("EU", settings=settings, connector=connector)


def log_dir(settings: Settings) -> Path:
    """Return the directory the EU run logs are written to.

    Args:
        settings: The test settings.

    Returns:
        The directory, which may not exist yet.
    """
    return settings.corpus_store_dir / "EU" / LOG_DIR_NAME


def logs(settings: Settings) -> list[Path]:
    """Return the run logs on disk, oldest name first.

    Args:
        settings: The test settings.

    Returns:
        The files.
    """
    return sorted(log_dir(settings).iterdir())


def only_log(settings: Settings) -> str:
    """Return the text of the single log a run left.

    Args:
        settings: The test settings.

    Returns:
        The file's contents.
    """
    written = logs(settings)
    assert len(written) == 1
    return written[0].read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------------------


class TestNaming:
    """A weekly job must never overwrite last week's record, or its own."""

    def test_the_name_carries_the_iso_week_and_the_instant(self) -> None:
        name = run_log_name(datetime(2026, 8, 9, 3, 12, 5, tzinfo=UTC))

        assert name == "2026-W32_20260809T031205Z.log"
        assert RUN_LOG_PATTERN.fullmatch(name) is not None

    def test_a_local_instant_is_named_in_utc(self) -> None:
        """Amsterdam in August is UTC+2, and a store may be read from anywhere."""
        local = datetime(2026, 8, 9, 5, 12, 5, tzinfo=timezone(timedelta(hours=2)))

        assert run_log_name(local) == "2026-W32_20260809T031205Z.log"

    def test_two_weeks_cannot_take_the_same_name(self) -> None:
        first = run_log_name(datetime(2026, 8, 9, 3, 0, tzinfo=UTC))
        second = run_log_name(datetime(2026, 8, 16, 3, 0, tzinfo=UTC))

        assert first != second
        assert first < second

    def test_the_names_sort_into_chronological_order_across_a_year_boundary(self) -> None:
        # 31 December 2026 is in ISO week 2027-W01, so the week alone would sort it before
        # December's earlier runs if the weeks were not contiguous. They are.
        december = run_log_name(datetime(2026, 12, 20, 3, 0, tzinfo=UTC))
        new_year = run_log_name(datetime(2026, 12, 31, 3, 0, tzinfo=UTC))
        january = run_log_name(datetime(2027, 1, 4, 3, 0, tzinfo=UTC))

        assert sorted([january, new_year, december]) == [december, new_year, january]

    def test_two_runs_in_the_same_second_each_keep_their_record(self, tmp_path: Path) -> None:
        store = CorpusStore(tmp_path, "EU")
        store.prepare()
        name = run_log_name(datetime(2026, 8, 9, 3, 12, 5, tzinfo=UTC))

        first = store.write_run_log(name, "the first run")
        second = store.write_run_log(name, "the second run")

        assert first != second
        assert first.read_text(encoding="utf-8") == "the first run"
        assert second.read_text(encoding="utf-8") == "the second run"
        # The suffix sorts after the plain name, so a listing stays in the order they ran.
        assert sorted([second.name, first.name]) == [first.name, second.name]
        assert RUN_LOG_PATTERN.fullmatch(second.name) is not None


# --------------------------------------------------------------------------------------
# A log exists, whatever happened
# --------------------------------------------------------------------------------------


class TestALogAlwaysExists:
    """A log that only appears on success is useless for the failure it exists to catch."""

    def test_a_successful_run_leaves_one_log_under_the_jurisdiction(
        self, settings: Settings
    ) -> None:
        run(settings, eu_documents(3))

        assert log_dir(settings).is_dir()
        assert only_log(settings).startswith("EU corpus mirror - ")

    def test_a_failed_run_leaves_one_too_and_says_why(self, settings: Settings) -> None:
        docs = eu_documents(3)

        def die(source_id: str) -> None:
            if source_id == docs[2].source_id:
                message = "the source went away"
                raise SourceUnavailableError(message)

        run(settings, docs, on_fetch=die)
        text = only_log(settings)

        assert "FAILED" in text
        assert "the source went away" in text

    def test_an_interrupted_run_leaves_one_and_says_nothing_is_lost(
        self, settings: Settings
    ) -> None:
        docs = eu_documents(4)

        def interrupt(source_id: str) -> None:
            if source_id == docs[1].source_id:
                signal.raise_signal(signal.SIGINT)

        run(settings, docs, on_fetch=interrupt)
        text = only_log(settings)

        assert "INTERRUPTED" in text
        assert "the next run resumes" in text.lower()

    def test_an_error_nobody_planned_for_still_leaves_a_record(self, settings: Settings) -> None:
        """The unhandled path is exactly the one an unattended job needs a record of."""
        connector = EuConnector(settings, docs=eu_documents(2), raise_on_discover=RuntimeError("x"))

        with pytest.raises(RuntimeError):
            mirror_jurisdiction("EU", settings=settings, connector=connector)

        text = only_log(settings)
        assert "FAILED" in text
        assert "RuntimeError" in text

    def test_a_log_that_cannot_be_written_does_not_cost_the_run_its_cases(
        self, settings: Settings
    ) -> None:
        store = CorpusStore(settings.corpus_store_dir, "EU")
        store.prepare()
        # A file where the log directory belongs: the directory cannot be created.
        (store.path / LOG_DIR_NAME).write_text("in the way", encoding="utf-8")

        run(settings, eu_documents(2))

        assert store.count_cases() == 2
        assert (store.path / "manifest.json").is_file()


# --------------------------------------------------------------------------------------
# What it says
# --------------------------------------------------------------------------------------


class TestWhatItSays:
    """Written for a human opening it cold, weeks later."""

    def test_it_separates_what_was_fetched_from_what_was_already_held(
        self, settings: Settings
    ) -> None:
        docs = eu_documents(3)
        run(settings, docs)
        run(settings, docs)
        second = logs(settings)[-1].read_text(encoding="utf-8")

        # The distinction is the whole point: a quiet week and a run that did nothing look
        # identical in the store and are opposites in the log.
        assert "quiet week" in second
        assert "0 fetched and written" in second
        assert "1 skipped without a single request" in second

    def test_it_states_the_window_before_and_after(self, settings: Settings) -> None:
        docs = eu_documents(3)
        run(settings, docs)
        run(settings, docs)
        second = logs(settings)[-1].read_text(encoding="utf-8")

        assert "Resumed from" in second
        assert "Left at" in second
        assert docs[-1].modified_at.isoformat() in second

    def test_a_first_run_says_there_was_no_position_to_resume_from(
        self, settings: Settings
    ) -> None:
        run(settings, eu_documents(2))

        assert "first run against this store" in only_log(settings)

    def test_it_states_how_long_the_run_took_and_how_it_ended(self, settings: Settings) -> None:
        run(settings, eu_documents(2))
        text = only_log(settings)

        assert "Started" in text
        assert "Finished" in text
        assert "Took" in text
        assert IngestStatus.SUCCESS.value in text

    def test_it_states_what_the_store_holds_afterwards(self, settings: Settings) -> None:
        run(settings, eu_documents(4))

        assert "Store now holds  4 cases on disk" in only_log(settings)

    def test_it_summarises_the_failures_and_points_at_the_full_record(
        self, settings: Settings
    ) -> None:
        docs = eu_documents(4)
        run(settings, docs, fail_fetch=frozenset({docs[1].source_id}))
        text = only_log(settings)

        assert "1 failed" in text
        assert "DocumentUnavailableError" in text
        assert docs[1].source_id in text
        assert "_failures.jsonl" in text

    def test_a_run_where_everything_failed_lists_a_bounded_number_of_cases(
        self, settings: Settings
    ) -> None:
        docs = eu_documents(30)
        run(settings, docs, fail_fetch=frozenset(document.source_id for document in docs))
        text = only_log(settings)

        # Every failure is counted and every one is in _failures.jsonl; the log prints a
        # sample, because thirty thousand lines is not a record anybody reads.
        assert "30  DocumentUnavailableError" in text
        assert text.count("DocumentUnavailableError: ") == 20
        assert "and 10 more" in text

    def test_it_reports_the_traffic_the_connector_kept(self, settings: Settings) -> None:
        connector = TalkativeConnector(settings, docs=eu_documents(2))

        mirror_jurisdiction("EU", settings=settings, connector=connector)
        text = only_log(settings)

        assert "112 requests sent, 3 of them retries" in text
        assert "1 wait honoured as asked, 30s in total" in text

    def test_a_connector_that_keeps_no_count_is_not_reported_as_silent(
        self, settings: Settings
    ) -> None:
        run(settings, eu_documents(1))

        assert "not reported by this connector" in only_log(settings)

    def test_a_rehearsal_says_it_was_one(self, settings: Settings) -> None:
        connector = EuConnector(settings, docs=eu_documents(5))

        mirror_jurisdiction("EU", settings=settings, connector=connector, limit=2)

        assert "--limit 2" in only_log(settings)


# --------------------------------------------------------------------------------------
# The store contract
# --------------------------------------------------------------------------------------


class TestTheStoreContract:
    """``logs/`` is additive: everything the store already promised still means what it did."""

    def test_the_existing_bookkeeping_is_untouched(self, settings: Settings) -> None:
        docs = eu_documents(3)
        run(settings, docs, fail_fetch=frozenset({docs[1].source_id}))
        store = CorpusStore(settings.corpus_store_dir, "EU")

        assert (store.path / "manifest.json").is_file()
        assert (store.path / "_checkpoint.json").is_file()
        assert (store.path / "_failures.jsonl").is_file()
        assert store.read_checkpoint("eurlex") is not None
        assert store.count_cases() == 2

    def test_the_log_directory_is_not_mistaken_for_a_case(self, settings: Settings) -> None:
        run(settings, eu_documents(2))
        store = CorpusStore(settings.corpus_store_dir, "EU")

        # count_cases reads folders holding a metadata.json, so logs/ cannot inflate the
        # total the manifest publishes.
        assert store.count_cases() == 2

    def test_a_case_cannot_be_stored_under_the_log_directory_s_name(self) -> None:
        with pytest.raises(CorpusStoreError):
            case_folder_name(LOG_DIR_NAME)


# --------------------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------------------


class TestRetention:
    """Unset means keep everything. A horizon is a decision somebody makes, not a default."""

    def written(self, store: CorpusStore, count: int) -> list[str]:
        """Write ``count`` logs a week apart and return their names.

        Args:
            store: The store to write into.
            count: How many weekly runs to simulate.

        Returns:
            The names written, oldest first.
        """
        return [
            store.write_run_log(run_log_name(EPOCH + timedelta(weeks=week)), f"run {week}").name
            for week in range(count)
        ]

    def test_the_default_keeps_every_log(self, settings: Settings) -> None:
        for _ in range(3):
            run(settings, eu_documents(1))

        assert settings.corpus_log_retention_runs is None
        assert len(logs(settings)) == 3

    def test_a_configured_horizon_keeps_the_newest_and_deletes_the_rest(
        self, tmp_path: Path
    ) -> None:
        store = CorpusStore(tmp_path, "EU")
        store.prepare()
        names = self.written(store, 5)

        removed = prune_run_logs(store, 2)

        assert removed == 3
        assert sorted(path.name for path in store.run_logs()) == names[-2:]

    def test_a_horizon_wider_than_the_history_deletes_nothing(self, tmp_path: Path) -> None:
        store = CorpusStore(tmp_path, "EU")
        store.prepare()
        self.written(store, 2)

        assert prune_run_logs(store, 10) == 0
        assert len(store.run_logs()) == 2

    def test_a_file_this_project_did_not_write_is_never_deleted(self, tmp_path: Path) -> None:
        store = CorpusStore(tmp_path, "EU")
        store.prepare()
        self.written(store, 3)
        (store.log_dir / "notes-from-the-operator.txt").write_text("keep me", encoding="utf-8")

        prune_run_logs(store, 1)

        assert (store.log_dir / "notes-from-the-operator.txt").is_file()

    def test_retention_applies_to_a_real_run(self, tmp_path: Path) -> None:
        settings = build_settings(
            corpus_store_dir=tmp_path / "CaseLawStore", corpus_log_retention_runs=1
        )
        store = CorpusStore(settings.corpus_store_dir, "EU")
        store.prepare()
        self.written(store, 4)

        run(settings, eu_documents(1))

        assert len(logs(settings)) == 1


@pytest.fixture(autouse=True)
def _no_signal_leak() -> Iterator[None]:
    """Restore the default ``SIGINT`` handler around every test in this module."""
    previous = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGINT, previous)
