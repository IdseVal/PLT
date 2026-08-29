"""The runner: the acceptance criteria of the pipeline, exercised end to end.

Everything here drives a fake connector against a temporary SQLite database — no network
(``CONTRIBUTING.md`` section 4) — and asserts on what the database and the checkpoint look
like afterwards, because those are what the next weekly run depends on.

The interruption test raises a real ``SIGINT`` in the middle of a run rather than setting the
flag by hand: the requirement is that the signal is trapped, and a test that sets the flag
itself would pass even if the handler were never installed.
"""

from __future__ import annotations

import json
import signal
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from plt.config import Settings
from plt.db.base import Base
from plt.db.models import (
    Case,
    CaseDocument,
    IngestRun,
    IngestStatus,
    Jurisdiction,
    JurisdictionType,
    KeywordMatch,
)
from plt.db.session import create_session_factory, make_engine
from plt.pipeline import registry
from plt.pipeline.base import SourceUnavailableError
from plt.pipeline.checkpoint import read_checkpoint
from plt.pipeline.filters.base import FilterChain
from plt.pipeline.filters.keywords import KeywordListNotFoundError
from plt.pipeline.runner import IngestReport, run_jurisdiction
from tests.conftest import build_settings
from tests.fakes import (
    EPOCH,
    UNRELATED_TEXT,
    FakeConnector,
    FakeDocument,
    documents,
)


@dataclass
class Harness:
    """A temporary database and the settings a run is driven with."""

    settings: Settings
    factory: sessionmaker[Session]

    def run(self, connector: FakeConnector, **overrides: object) -> IngestReport:
        """Run the connector against the harness's database."""
        options: dict[str, object] = {
            "connector": connector,
            "settings": self.settings,
            "session_factory": self.factory,
        }
        options.update(overrides)
        return run_jurisdiction("NL", **options)  # type: ignore[arg-type]

    def session(self) -> Session:
        """Open a session on the harness's database."""
        return self.factory()

    def count(self, entity: type[Case] | type[CaseDocument] | type[KeywordMatch]) -> int:
        """Count the rows of a table."""
        with self.session() as session:
            return session.execute(select(func.count()).select_from(entity)).scalar_one()


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    """Build a temporary database with the two launch jurisdictions seeded."""
    settings = build_settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'plt.db'}",
        pipeline_batch_size=2,
        pipeline_report_dir=tmp_path / "reports",
    )
    engine: Engine = make_engine(settings)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        session.add_all(
            [
                Jurisdiction(
                    code="NL", name="Netherlands", type=JurisdictionType.STATE, map_feature_id="NL"
                ),
                Jurisdiction(
                    code="EU",
                    name="European Union",
                    type=JurisdictionType.SUPRANATIONAL,
                    map_feature_id="EU",
                ),
            ]
        )
        session.commit()
    try:
        yield Harness(settings=settings, factory=factory)
    finally:
        engine.dispose()


# -- Ingestion ------------------------------------------------------------------------


def test_a_matching_document_is_stored_with_its_text_and_its_matches(harness: Harness) -> None:
    report = harness.run(FakeConnector(docs=documents(1)))

    assert report.status is IngestStatus.SUCCESS
    assert report.counters.inserted == 1
    assert report.counters.matched == 1
    assert harness.count(Case) == 1
    assert harness.count(CaseDocument) == 1
    assert harness.count(KeywordMatch) >= 1

    with harness.session() as session:
        stored = session.scalars(select(Case)).one()
        assert stored.content_hash is not None
        assert stored.revision == 1
        assert stored.court is not None
        assert stored.source_metadata == {}
        assert stored.documents[0].full_text is not None


def test_a_document_the_filter_rejects_is_not_stored(harness: Harness) -> None:
    report = harness.run(FakeConnector(docs=documents(3, text=UNRELATED_TEXT)))

    assert report.counters.rejected == 3
    assert report.counters.matched == 0
    assert report.counters.inserted == 0
    assert harness.count(Case) == 0


def test_the_subject_alone_can_qualify_a_case(harness: Harness) -> None:
    """The rechtsgebied is a curated signal; a connector that fills it in gets recall for it."""
    document = FakeDocument(
        source_id="ECLI:NL:RBTEST:2026:9",
        text=UNRELATED_TEXT,
        subject="Gewasbeschermingsmiddelen",
    )

    report = harness.run(FakeConnector(docs=[document]))

    assert report.counters.inserted == 1
    with harness.session() as session:
        stored = session.scalars(select(Case)).one()
        assert stored.source_metadata["subject"] == "Gewasbeschermingsmiddelen"
        assert {match.field for match in stored.keyword_matches} == {"subject"}


# -- Deduplication --------------------------------------------------------------------


def test_running_the_same_window_twice_inserts_nothing_the_second_time(
    harness: Harness,
) -> None:
    docs = documents(5)

    first = harness.run(FakeConnector(docs=docs))
    second = harness.run(FakeConnector(docs=docs), since=EPOCH)

    assert first.counters.inserted == 5
    assert second.counters.inserted == 0
    assert second.counters.updated == 0
    assert second.counters.skipped_duplicate == 5
    assert harness.count(Case) == 5


def test_an_unchanged_document_with_a_discovery_hash_is_never_fetched_again(
    harness: Harness,
) -> None:
    docs = documents(3, with_hash=True)
    harness.run(FakeConnector(docs=docs))

    second = FakeConnector(docs=docs)
    report = harness.run(second, since=EPOCH)

    assert second.discovered == [document.source_id for document in docs]
    assert second.fetched == []
    assert report.counters.fetched == 0
    assert report.counters.skipped_duplicate == 3


def test_an_unchanged_document_only_has_its_last_seen_touched(harness: Harness) -> None:
    docs = documents(1, with_hash=True)
    harness.run(FakeConnector(docs=docs))
    with harness.session() as session:
        before = session.scalars(select(Case)).one()
        first_seen, seen_once, revision = before.first_seen_at, before.last_seen_at, before.revision

    harness.run(FakeConnector(docs=docs), since=EPOCH)

    with harness.session() as session:
        after = session.scalars(select(Case)).one()
        assert after.first_seen_at == first_seen
        assert after.last_seen_at >= seen_once
        assert after.revision == revision


def test_a_revised_document_is_updated_in_place(harness: Harness) -> None:
    original = FakeDocument(source_id="ECLI:NL:RBTEST:2026:1", title="Uitspraak")
    harness.run(FakeConnector(docs=[original]))

    revised = FakeDocument(
        source_id="ECLI:NL:RBTEST:2026:1",
        title="Uitspraak (gerectificeerd)",
        payload_suffix="<!-- corrected -->",
    )
    report = harness.run(FakeConnector(docs=[revised]), since=EPOCH)

    assert report.counters.updated == 1
    assert report.counters.inserted == 0
    assert harness.count(Case) == 1
    with harness.session() as session:
        stored = session.scalars(select(Case)).one()
        assert stored.revision == 2
        assert stored.title == "Uitspraak (gerectificeerd)"
        assert len(stored.documents) == 1


# -- Checkpoints ----------------------------------------------------------------------


def test_a_successful_run_advances_the_checkpoint(harness: Harness) -> None:
    docs = documents(3)

    report = harness.run(FakeConnector(docs=docs))

    assert report.checkpoint_after is not None
    assert report.checkpoint_after.last_modified_seen == docs[-1].modified_at
    with harness.session() as session:
        stored = read_checkpoint(session, "fake")
    assert stored is not None
    assert stored.last_modified_seen == docs[-1].modified_at
    assert stored.last_source_id == docs[-1].source_id


def test_the_next_run_starts_from_the_checkpoint(harness: Harness) -> None:
    docs = documents(4)
    harness.run(FakeConnector(docs=docs))

    later = FakeDocument(
        source_id="ECLI:NL:RBTEST:2026:later",
        modified_at=docs[-1].modified_at + timedelta(hours=1),
    )
    second = FakeConnector(docs=[*docs, later])
    report = harness.run(second)

    # The window opened at the checkpoint, so only the last stored document is offered
    # again - deduplication absorbs that overlap - plus the genuinely new one.
    assert second.discovered == [docs[-1].source_id, later.source_id]
    assert report.counters.inserted == 1
    assert report.counters.skipped_duplicate == 1


def test_a_failed_run_does_not_advance_the_checkpoint(harness: Harness) -> None:
    docs = documents(2)
    harness.run(FakeConnector(docs=docs))
    with harness.session() as session:
        before = read_checkpoint(session, "fake")

    broken = FakeConnector(
        docs=[FakeDocument(source_id="ECLI:NL:RBTEST:2026:new", modified_at=EPOCH)],
        raise_on_discover=SourceUnavailableError("data.rechtspraak.nl is down"),
    )
    report = harness.run(broken)

    assert report.status is IngestStatus.FAILED
    assert report.error_message is not None
    assert "SourceUnavailableError" in report.error_message
    with harness.session() as session:
        after = read_checkpoint(session, "fake")
    assert after == before


def test_a_failed_document_holds_the_checkpoint_back(harness: Harness) -> None:
    """Everything after a failure is retried next run, so nothing is silently lost."""
    docs = documents(4)
    connector = FakeConnector(docs=docs, fail_fetch=frozenset({docs[1].source_id}))

    report = harness.run(connector)

    assert report.status is IngestStatus.PARTIAL
    assert report.counters.errors == 1
    assert report.counters.inserted == 3
    assert report.checkpoint_after is not None
    assert report.checkpoint_after.last_modified_seen == docs[0].modified_at


def test_a_run_that_found_nothing_leaves_the_checkpoint_alone(harness: Harness) -> None:
    report = harness.run(FakeConnector(docs=[]))

    assert report.status is IngestStatus.SUCCESS
    assert report.counters.discovered == 0
    with harness.session() as session:
        assert read_checkpoint(session, "fake") is None


# -- Interruption ---------------------------------------------------------------------


def test_sigint_finishes_the_document_in_flight_and_checkpoints_it(harness: Harness) -> None:
    docs = documents(6)
    interrupt_at = docs[2].source_id

    def interrupt(source_id: str) -> None:
        if source_id == interrupt_at:
            signal.raise_signal(signal.SIGINT)

    connector = FakeConnector(docs=docs, on_fetch=interrupt)
    report = harness.run(connector)

    assert report.status is IngestStatus.INTERRUPTED
    # The document in flight was finished and committed; nothing after it was started.
    assert connector.fetched == [docs[0].source_id, docs[1].source_id, docs[2].source_id]
    assert harness.count(Case) == 3
    with harness.session() as session:
        stored = read_checkpoint(session, "fake")
    assert stored is not None
    assert stored.last_modified_seen == docs[2].modified_at


def test_a_run_resumes_from_where_the_interruption_left_it(harness: Harness) -> None:
    docs = documents(6)

    def interrupt(source_id: str) -> None:
        if source_id == docs[2].source_id:
            signal.raise_signal(signal.SIGINT)

    harness.run(FakeConnector(docs=docs, on_fetch=interrupt))
    resumed = FakeConnector(docs=docs)
    report = harness.run(resumed)

    assert report.status is IngestStatus.SUCCESS
    assert resumed.discovered == [document.source_id for document in docs[2:]]
    assert report.counters.inserted == 3
    assert report.counters.skipped_duplicate == 1
    assert harness.count(Case) == 6


def test_the_interrupted_run_is_recorded_as_interrupted(harness: Harness) -> None:
    docs = documents(4)

    def interrupt(source_id: str) -> None:
        if source_id == docs[0].source_id:
            signal.raise_signal(signal.SIGINT)

    harness.run(FakeConnector(docs=docs, on_fetch=interrupt))

    with harness.session() as session:
        run = session.scalars(select(IngestRun)).one()
    assert run.status is IngestStatus.INTERRUPTED
    assert run.finished_at is not None


def test_the_signal_handler_is_restored_afterwards(harness: Harness) -> None:
    before = signal.getsignal(signal.SIGINT)

    harness.run(FakeConnector(docs=documents(1)))

    assert signal.getsignal(signal.SIGINT) is before


# -- Run bookkeeping ------------------------------------------------------------------


def test_the_run_row_carries_accurate_counts(harness: Harness) -> None:
    docs = [
        *documents(2),
        FakeDocument(source_id="ECLI:NL:RBTEST:2026:x", text=UNRELATED_TEXT),
        FakeDocument(source_id="ECLI:NL:RBTEST:2026:y"),
    ]
    connector = FakeConnector(docs=docs, fail_normalise=frozenset({"ECLI:NL:RBTEST:2026:y"}))

    report = harness.run(connector)

    with harness.session() as session:
        run = session.scalars(select(IngestRun)).one()
    assert run.status is IngestStatus.PARTIAL
    assert run.fetched_count == 4
    assert run.matched_count == 2
    assert run.inserted_count == 2
    assert run.updated_count == 0
    assert run.skipped_duplicate_count == 0
    assert run.error_count == 1
    assert run.checkpoint_before is None
    assert run.checkpoint_after is not None
    assert run.finished_at is not None
    assert report.counters.rejected == 1


def test_a_failed_run_records_the_counters_it_accumulated(harness: Harness) -> None:
    """The failure is injected midway, so the counters have something to lose.

    A run that fetched documents and then died must not be indistinguishable from one that
    never got off the ground: the failed runs are precisely the ones an investigator reads
    ``ingest_run`` for.
    """
    docs = documents(4)
    connector = FakeConnector(
        docs=docs,
        on_fetch=lambda source_id: _fail_source(source_id, docs[2].source_id),
    )

    report = harness.run(connector, batch_size=2)

    assert report.status is IngestStatus.FAILED
    assert report.error_message is not None
    assert report.counters.discovered == 4
    assert report.counters.fetched == 2
    assert report.counters.matched == 2
    assert report.counters.inserted == 2

    with harness.session() as session:
        run = session.scalars(select(IngestRun)).one()
    assert run.status is IngestStatus.FAILED
    assert run.error_message is not None
    assert run.fetched_count == 2
    assert run.matched_count == 2
    assert run.inserted_count == 2
    assert run.updated_count == 0
    assert run.skipped_duplicate_count == 0
    assert run.error_count == 0
    # The verdict is still a failure, and the checkpoint still did not move.
    assert run.checkpoint_after == run.checkpoint_before


def test_a_failed_run_records_the_documents_that_failed_before_it(harness: Harness) -> None:
    """Per-document errors counted before the fatal one survive it too."""
    docs = documents(4)
    connector = FakeConnector(
        docs=docs,
        fail_fetch=frozenset({docs[0].source_id}),
        on_fetch=lambda source_id: _fail_source(source_id, docs[3].source_id),
    )

    report = harness.run(connector, batch_size=2)

    assert report.status is IngestStatus.FAILED
    assert report.counters.errors == 1

    with harness.session() as session:
        run = session.scalars(select(IngestRun)).one()
    assert run.error_count == 1
    assert run.fetched_count == 2


def test_an_interrupted_run_records_the_counters_it_accumulated(harness: Harness) -> None:
    """The signal path has to carry the counters too (core document 2.6)."""
    docs = documents(6)

    def interrupt(source_id: str) -> None:
        if source_id == docs[2].source_id:
            signal.raise_signal(signal.SIGINT)

    report = harness.run(FakeConnector(docs=docs, on_fetch=interrupt), batch_size=2)

    assert report.status is IngestStatus.INTERRUPTED
    assert report.counters.fetched == 3
    assert report.counters.inserted == 3

    with harness.session() as session:
        run = session.scalars(select(IngestRun)).one()
    assert run.status is IngestStatus.INTERRUPTED
    assert run.fetched_count == 3
    assert run.inserted_count == 3


def test_a_second_run_records_the_checkpoint_it_started_from(harness: Harness) -> None:
    docs = documents(2)
    harness.run(FakeConnector(docs=docs))
    harness.run(FakeConnector(docs=docs))

    with harness.session() as session:
        runs = session.scalars(select(IngestRun).order_by(IngestRun.id)).all()
    assert runs[0].checkpoint_before is None
    assert runs[1].checkpoint_before is not None
    assert runs[1].checkpoint_before["last_source_id"] == docs[-1].source_id


def test_a_document_failure_never_aborts_the_run(harness: Harness) -> None:
    docs = documents(5)
    connector = FakeConnector(
        docs=docs,
        fail_fetch=frozenset({docs[0].source_id}),
        fail_normalise=frozenset({docs[3].source_id}),
    )

    report = harness.run(connector)

    assert report.counters.errors == 2
    assert report.counters.inserted == 3
    assert harness.count(Case) == 3


def test_the_runner_closes_a_connector_it_built_itself(harness: Harness) -> None:
    """The registry path, end to end: no connector is passed in, so the runner owns it."""
    built: list[FakeConnector] = []

    class Recording(FakeConnector):
        """A connector that records the instances the registry builds."""

        name = "recording"

        def __init__(self, settings: Settings | None = None) -> None:
            """Build the fake with a fixed document set and remember the instance."""
            super().__init__(settings, docs=documents(1), raise_on_discover=RuntimeError("boom"))
            built.append(self)

    registry.reset_registry(Recording)
    try:
        report = run_jurisdiction("NL", settings=harness.settings, session_factory=harness.factory)
    finally:
        registry.reset_registry()

    assert report.status is IngestStatus.FAILED
    assert len(built) == 1
    assert built[0].closed


def test_an_unknown_jurisdiction_fails_the_run(harness: Harness) -> None:
    class Elsewhere(FakeConnector):
        """A connector for a jurisdiction nobody seeded."""

        jurisdiction_code = "ZZ"
        name = "elsewhere"

    # An empty chain, because a jurisdiction with no row has no keyword list either, and
    # the missing list would be reported first.
    report = harness.run(Elsewhere(docs=documents(1)), chain=FilterChain())

    assert report.status is IngestStatus.FAILED
    assert report.error_message is not None
    assert "not in the database" in report.error_message


def test_a_jurisdiction_without_a_keyword_list_is_a_caller_error(harness: Harness) -> None:
    """Onboarding order: the list is curated before the first run, never defaulted away."""

    class Elsewhere(FakeConnector):
        """A connector for a jurisdiction with no curated list."""

        jurisdiction_code = "ZZ"
        name = "elsewhere"

    with pytest.raises(KeywordListNotFoundError):
        harness.run(Elsewhere(docs=documents(1)))


# -- Dry run --------------------------------------------------------------------------


def test_a_dry_run_writes_nothing_to_the_database(harness: Harness, tmp_path: Path) -> None:
    report = harness.run(
        FakeConnector(docs=documents(3)),
        dry_run=True,
        report_path=tmp_path / "report.jsonl",
    )

    assert report.counters.matched == 3
    assert report.counters.inserted == 0
    assert harness.count(Case) == 0
    with harness.session() as session:
        assert session.scalars(select(IngestRun)).all() == []
        assert read_checkpoint(session, "fake") is None


def test_a_dry_run_reports_which_cases_passed_and_on_which_terms(
    harness: Harness, tmp_path: Path
) -> None:
    path = tmp_path / "report.jsonl"
    docs = [*documents(2), FakeDocument(source_id="ECLI:NL:RBTEST:2026:x", text=UNRELATED_TEXT)]

    harness.run(FakeConnector(docs=docs), dry_run=True, report_path=path)

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    header, *entries = lines
    assert header["type"] == "run"
    assert header["jurisdiction"] == "NL"
    assert [entry["passed"] for entry in entries] == [True, True, False]
    passed = entries[0]
    assert passed["action"] == "insert"
    assert passed["matched_term_count"] >= 1
    assert "nl-gewasbeschermingsmiddel" in {term["term_id"] for term in passed["terms"]}
    assert "gewasbeschermingsmiddel" in {term["term"] for term in passed["terms"]}
    assert "product_class" in {term["category"] for term in passed["terms"]}
    assert entries[2]["action"] == "reject"


def test_a_dry_run_defaults_its_report_into_the_configured_directory(harness: Harness) -> None:
    report = harness.run(FakeConnector(docs=documents(1)), dry_run=True)

    assert report.report_path is not None
    assert report.report_path.parent == harness.settings.pipeline_report_dir
    assert report.report_path.exists()


def test_a_dry_run_still_reads_the_database_for_duplicates(harness: Harness) -> None:
    docs = documents(2, with_hash=True)
    harness.run(FakeConnector(docs=docs))

    connector = FakeConnector(docs=docs)
    report = harness.run(connector, since=EPOCH, dry_run=True)

    assert report.counters.skipped_duplicate == 2
    assert connector.fetched == []


# -- Batching -------------------------------------------------------------------------


def test_work_is_committed_per_batch(harness: Harness) -> None:
    """The batch that completed before a fatal error stays committed."""
    docs = documents(4)
    connector = FakeConnector(
        docs=docs,
        on_fetch=lambda source_id: _fail_source(source_id, docs[2].source_id),
    )

    report = harness.run(connector, batch_size=2)

    assert report.status is IngestStatus.FAILED
    assert harness.count(Case) == 2


def _fail_source(source_id: str, failing: str) -> None:
    """Raise a fatal source error when a particular document is reached."""
    if source_id == failing:
        message = "the source stopped answering"
        raise SourceUnavailableError(message)
