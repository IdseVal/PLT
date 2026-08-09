"""The targeted repair: list, diff, fetch only the difference.

Everything here drives a fake connector against a temporary store — no network
(``CONTRIBUTING.md`` section 4). What is under test is not "does it fetch cases"; the mirror
tests cover that, and a repair fetches through the same code. It is the three claims the mode
is worth having for (``docs/architecture.md`` rule 2.10 and section 9):

* it asks the source for a **listing** and never for a discovery walk, and it fetches exactly
  the cases the store does not hold;
* it leaves the capture's own resume position untouched, so a repair can never make a later
  capture skip a window it did not walk;
* a failure, an interruption and a source that refuses are each recorded rather than passed
  off as a corpus that is now complete.

The interruption test raises a real ``SIGINT`` mid-run rather than setting the flag by hand,
for the same reason the mirror's does: the requirement is that the signal is *trapped*.
"""

from __future__ import annotations

import json
import signal
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from plt.config import Settings
from plt.db.models import IngestStatus
from plt.pipeline.base import Candidate, IdentifierListUnavailableError, SourceUnavailableError
from plt.pipeline.mirror import CorpusStore, MirrorReport, mirror_jurisdiction
from plt.pipeline.repair import repair_jurisdiction
from plt.pipeline.runlog import RunMode
from tests.conftest import build_settings
from tests.fakes import EPOCH, FakeConnector, FakeDocument, documents

# --------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------


class ListingConnector(FakeConnector):
    """A fake source that can state what it holds without being walked.

    Attributes:
        listed: Identifiers ``enumerate_identifiers`` yielded, in order — the evidence that a
            repair listed rather than discovered.
        listing_error: Raised part-way through the listing, standing in for an endpoint that
            refuses us mid-way.
        listing_fails_after: How many identifiers to list before raising it.
    """

    jurisdiction_code = "EU"
    name = "eurlex"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        listing_error: Exception | None = None,
        listing_fails_after: int = 0,
        **kwargs: object,
    ) -> None:
        """Build the fake.

        Args:
            settings: Settings, as the registry passes them.
            listing_error: Error raised part-way through the listing, or ``None``.
            listing_fails_after: Identifiers listed before that error is raised.
            **kwargs: Passed to :class:`~tests.fakes.FakeConnector`.
        """
        super().__init__(settings, **kwargs)  # type: ignore[arg-type]
        self.listed: list[str] = []
        self.listing_error = listing_error
        self.listing_fails_after = listing_fails_after

    def enumerate_identifiers(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> Iterator[Candidate]:
        """Yield the identifiers the fake source holds, carrying nothing else.

        Args:
            since: Lower bound on the modification instant, applied as a real listing would.
            until: Upper bound.

        Yields:
            One bare candidate per document.

        Raises:
            Exception: ``listing_error``, once ``listing_fails_after`` have been listed.
        """
        for document in self.docs:
            if since is not None and document.modified_at < since:
                continue
            if until is not None and document.modified_at > until:
                continue
            if self.listing_error is not None and len(self.listed) >= self.listing_fails_after:
                raise self.listing_error
            self.listed.append(document.source_id)
            yield Candidate(
                source_id=document.source_id,
                jurisdiction_code=self.jurisdiction_code,
                cursor=f"listing:{len(self.listed)}",
                source_url=f"https://example.invalid/{document.source_id}",
            )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return settings whose corpus store is a temporary directory."""
    return build_settings(corpus_store_dir=tmp_path / "CaseLawStore", pipeline_batch_size=2)


def eu_documents(count: int = 5) -> list[FakeDocument]:
    """Build CELEX-shaped documents, one minute apart, oldest first.

    Args:
        count: How many to build.

    Returns:
        The documents the fake source holds.
    """
    return [
        FakeDocument(
            source_id=f"6202{index % 5}CJ{index:04d}",
            modified_at=document.modified_at,
            text=document.text,
            title=f"Judgment {index}",
            language="en",
        )
        for index, document in enumerate(documents(count))
    ]


def store_for(settings: Settings) -> CorpusStore:
    """Return the EU store under the temporary root.

    Args:
        settings: The test settings.

    Returns:
        The store.
    """
    return CorpusStore(settings.corpus_store_dir, "EU")


def capture(settings: Settings, docs: Sequence[FakeDocument]) -> MirrorReport:
    """Mirror a source the ordinary way, to make a store worth repairing.

    Args:
        settings: The test settings.
        docs: The documents the source holds during the capture.

    Returns:
        The capture's report.
    """
    return mirror_jurisdiction(
        "EU", settings=settings, connector=ListingConnector(settings, docs=docs)
    )


def repair(
    settings: Settings,
    docs: Sequence[FakeDocument],
    **overrides: object,
) -> tuple[MirrorReport, ListingConnector]:
    """Repair the store against a source holding ``docs``.

    Args:
        settings: The test settings.
        docs: Everything the source holds now, which is what the listing states.
        **overrides: Keyword arguments for the connector, e.g. ``fail_fetch``.

    Returns:
        The run's report and the connector it drove.
    """
    connector = ListingConnector(settings, docs=docs, **overrides)  # type: ignore[arg-type]
    report = repair_jurisdiction("EU", settings=settings, connector=connector)
    return report, connector


def partial_store(settings: Settings, *, held: int = 3, total: int = 5) -> list[FakeDocument]:
    """Capture part of a corpus, leaving the rest of it missing.

    Args:
        settings: The test settings.
        held: How many cases the store ends up holding.
        total: How many the source holds.

    Returns:
        Everything the source holds, the captured ones first.
    """
    docs = eu_documents(total)
    capture(settings, docs[:held])
    return docs


# --------------------------------------------------------------------------------------
# The diff: what a repair asks for, and what it pays for
# --------------------------------------------------------------------------------------


class TestTheDifference:
    """A repair fetches the cases the store lacks, and pays nothing for the rest."""

    def test_only_the_missing_cases_are_fetched(self, settings: Settings) -> None:
        docs = partial_store(settings, held=3, total=5)

        report, connector = repair(settings, docs)

        assert connector.listed == [document.source_id for document in docs]
        assert connector.fetched == [document.source_id for document in docs[3:]]
        assert report.counters.discovered == 5
        assert report.counters.skipped == 3
        assert report.counters.mirrored == 2
        assert report.counters.errors == 0
        assert report.status is IngestStatus.SUCCESS
        assert store_for(settings).count_cases() == 5

    def test_the_listing_is_used_and_the_walk_is_not(self, settings: Settings) -> None:
        docs = partial_store(settings, held=3, total=5)

        _, connector = repair(settings, docs)

        assert connector.listed, "the repair must ask the source for its identifiers"
        assert connector.discovered == [], "a repair must never walk discovery"

    def test_a_complete_corpus_costs_the_listing_and_nothing_else(self, settings: Settings) -> None:
        docs = eu_documents(4)
        capture(settings, docs)

        report, connector = repair(settings, docs)

        assert connector.fetched == []
        assert report.counters.skipped == 4
        assert report.counters.mirrored == 0
        assert report.status is IngestStatus.SUCCESS

    def test_a_repaired_case_is_stored_exactly_as_a_captured_one(self, settings: Settings) -> None:
        docs = partial_store(settings, held=3, total=5)

        repair(settings, docs)

        store = store_for(settings)
        repaired = store.case_dir(docs[4].source_id)
        record = json.loads((repaired / "metadata.json").read_text(encoding="utf-8"))
        assert record["identifier"] == docs[4].source_id
        assert record["connector"] == "eurlex"
        assert record["normalised"] is True
        assert (repaired / "raw_content.xml").is_file()

    def test_the_listing_can_be_narrowed_to_a_band(self, settings: Settings) -> None:
        """The Dutch shape: a gap known to lie in a date range, repaired precisely."""
        docs = eu_documents(6)
        capture(settings, [docs[0], docs[5]])
        connector = ListingConnector(settings, docs=docs)

        report = repair_jurisdiction(
            "EU",
            docs[2].modified_at,
            docs[3].modified_at,
            settings=settings,
            connector=connector,
        )

        assert connector.listed == [docs[2].source_id, docs[3].source_id]
        assert connector.fetched == [docs[2].source_id, docs[3].source_id]
        assert report.counters.discovered == 2
        assert report.counters.skipped == 0

    def test_a_limit_stops_the_repair_early(self, settings: Settings) -> None:
        docs = partial_store(settings, held=1, total=5)

        connector = ListingConnector(settings, docs=docs)
        report = repair_jurisdiction("EU", settings=settings, limit=2, connector=connector)

        assert report.counters.mirrored == 2
        assert len(connector.fetched) == 2
        assert report.limit == 2


# --------------------------------------------------------------------------------------
# The position: a repair must not disturb the capture
# --------------------------------------------------------------------------------------


class TestThePosition:
    """A repair's position is a place in a listing, and is kept apart from the capture's."""

    def test_the_capture_checkpoint_is_left_exactly_as_it_was(self, settings: Settings) -> None:
        docs = partial_store(settings, held=3, total=5)
        store = store_for(settings)
        before = store.read_checkpoint("eurlex")
        assert before is not None

        repair(settings, docs)

        after = store.read_checkpoint("eurlex")
        assert after is not None
        assert after.last_modified_seen == before.last_modified_seen
        assert after.last_source_id == before.last_source_id
        assert after.last_cursor == before.last_cursor

    def test_the_repair_position_is_its_own_file(self, settings: Settings) -> None:
        docs = partial_store(settings, held=3, total=5)

        repair(settings, docs)

        store = store_for(settings)
        assert (store.path / "_repair_checkpoint.json").is_file()
        position = store.read_repair_checkpoint("eurlex")
        assert position is not None
        assert position.last_source_id == docs[-1].source_id

    def test_the_repair_position_carries_no_instant_to_be_mistaken_for_a_window(
        self, settings: Settings
    ) -> None:
        """Read as a window, a listing position would truncate the next capture silently."""
        docs = partial_store(settings, held=3, total=5)

        repair(settings, docs)

        position = store_for(settings).read_repair_checkpoint("eurlex")
        assert position is not None
        assert position.last_modified_seen is None

    def test_a_later_capture_still_resumes_where_it_did(self, settings: Settings) -> None:
        docs = partial_store(settings, held=3, total=5)
        repair(settings, docs)

        connector = ListingConnector(settings, docs=docs)
        mirror_jurisdiction("EU", settings=settings, connector=connector)

        # The capture resumed from its own checkpoint - the third case - rather than from
        # wherever the repair happened to stop.
        assert connector.discovered == [document.source_id for document in docs[2:]]

    def test_a_repair_is_resumed_by_the_store_rather_than_by_its_checkpoint(
        self, settings: Settings
    ) -> None:
        docs = partial_store(settings, held=1, total=5)
        first = ListingConnector(settings, docs=docs)
        repair_jurisdiction("EU", settings=settings, limit=2, connector=first)

        report, second = repair(settings, docs)

        assert second.listed == [document.source_id for document in docs]
        assert second.fetched == [document.source_id for document in docs[3:]]
        assert report.counters.skipped == 3
        assert store_for(settings).count_cases() == 5


# --------------------------------------------------------------------------------------
# What goes wrong, and whether it is recorded
# --------------------------------------------------------------------------------------


class TestWhenItGoesWrong:
    """A repair that did not close the gap must not read as one that did."""

    def test_a_case_that_fails_is_still_missing_and_says_so(self, settings: Settings) -> None:
        docs = partial_store(settings, held=3, total=5)

        report, _ = repair(settings, docs, fail_fetch=frozenset({docs[3].source_id}))

        assert report.status is IngestStatus.PARTIAL
        assert report.counters.errors == 1
        assert report.counters.mirrored == 1
        store = store_for(settings)
        assert store.holds(docs[3].source_id) is False
        failures = (store.path / "_failures.jsonl").read_text(encoding="utf-8")
        assert docs[3].source_id in failures

    def test_a_case_that_failed_does_not_hold_the_rest_of_the_listing_back(
        self, settings: Settings
    ) -> None:
        """Unlike a window, a listing has no position for one bad case to freeze."""
        docs = partial_store(settings, held=1, total=5)

        report, connector = repair(settings, docs, fail_fetch=frozenset({docs[1].source_id}))

        assert connector.fetched == [document.source_id for document in docs[1:]]
        assert report.counters.mirrored == 3
        assert store_for(settings).count_cases() == 4

    def test_a_failed_case_is_offered_again_by_the_next_repair(self, settings: Settings) -> None:
        docs = partial_store(settings, held=3, total=5)
        repair(settings, docs, fail_fetch=frozenset({docs[3].source_id}))

        report, connector = repair(settings, docs)

        assert docs[3].source_id in connector.fetched
        assert report.counters.mirrored == 1
        assert store_for(settings).count_cases() == 5

    def test_a_source_that_refuses_the_listing_ends_the_run_rather_than_looking_complete(
        self, settings: Settings
    ) -> None:
        docs = partial_store(settings, held=1, total=5)
        refusal = SourceUnavailableError("the source asked for 1800s before the next request")

        report, connector = repair(settings, docs, listing_error=refusal, listing_fails_after=2)

        assert report.status is IngestStatus.FAILED
        assert report.error_message is not None
        assert "1800s" in report.error_message
        assert len(connector.listed) == 2
        # Whatever it managed before the refusal is kept; nothing claims the gap is closed.
        assert store_for(settings).count_cases() == 2

    def test_a_connector_with_no_cheap_listing_is_refused_up_front(
        self, settings: Settings
    ) -> None:
        connector = FakeConnector(settings, docs=eu_documents(2))

        with pytest.raises(IdentifierListUnavailableError):
            repair_jurisdiction("NL", settings=settings, connector=connector)

    def test_sigint_finishes_the_case_in_flight_and_records_it(self, settings: Settings) -> None:
        docs = partial_store(settings, held=1, total=5)
        interrupt_at = docs[2].source_id

        def interrupt(source_id: str) -> None:
            if source_id == interrupt_at:
                signal.raise_signal(signal.SIGINT)

        report, connector = repair(settings, docs, on_fetch=interrupt)

        assert report.status is IngestStatus.INTERRUPTED
        assert connector.fetched == [document.source_id for document in docs[1:3]]
        store = store_for(settings)
        assert store.holds(interrupt_at) is True
        assert store.count_cases() == 3


# --------------------------------------------------------------------------------------
# The record a repair leaves
# --------------------------------------------------------------------------------------


class TestTheRecord:
    """A reader must be able to tell a repair from a capture, in every file it writes."""

    def test_the_run_log_is_written_and_says_it_is_a_repair(self, settings: Settings) -> None:
        docs = partial_store(settings, held=3, total=5)

        repair(settings, docs)

        logs = sorted(store_for(settings).log_dir.iterdir())
        text = logs[-1].read_text(encoding="utf-8")
        assert text.startswith("EU corpus repair - ")
        assert "5 identifiers the source holds" in text
        assert "2 the store did not hold" in text
        assert "3 skipped without a single request" in text
        assert "_repair_checkpoint.json" in text

    def test_a_log_is_written_even_when_the_source_refuses(self, settings: Settings) -> None:
        docs = partial_store(settings, held=1, total=5)

        repair(
            settings,
            docs,
            listing_error=SourceUnavailableError("no"),
            listing_fails_after=1,
        )

        text = sorted(store_for(settings).log_dir.iterdir())[-1].read_text(encoding="utf-8")
        assert "FAILED" in text
        assert "corpus repair" in text

    def test_the_manifest_records_the_repair_without_restating_the_capture(
        self, settings: Settings
    ) -> None:
        docs = eu_documents(5)
        mirror_jurisdiction(
            "EU",
            EPOCH,
            datetime(2026, 3, 1, tzinfo=UTC),
            settings=settings,
            connector=ListingConnector(settings, docs=docs[:3]),
        )
        store = store_for(settings)
        before = store.read_manifest()["capture"]

        repair(settings, docs)

        manifest = store.read_manifest()
        assert isinstance(before, dict)
        capture_block = manifest["capture"]
        assert isinstance(capture_block, dict)
        assert capture_block["window_since"] == before["window_since"]
        assert capture_block["window_until"] == before["window_until"]
        assert capture_block["status"] == before["status"]
        assert capture_block["updated_at"] != before["updated_at"]
        runs = manifest["runs"]
        assert isinstance(runs, list)
        assert [run["mode"] for run in runs] == ["mirror", "repair"]
        contents = manifest["contents"]
        assert isinstance(contents, dict)
        # A repair leaves the capture's claims alone and re-counts what is on disk, so the
        # cases it fetched are in the corpus's description without being in its window.
        assert contents["cases"] == 5

    def test_the_summary_line_names_the_mode(self, settings: Settings) -> None:
        docs = partial_store(settings, held=3, total=5)

        report, _ = repair(settings, docs)

        assert report.mode is RunMode.REPAIR
        assert report.summary().startswith("EU: repair success - 5 listed, 2 mirrored")
