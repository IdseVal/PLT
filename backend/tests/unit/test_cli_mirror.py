"""``plt mirror``: the command that takes a corpus, and the exit code it reports it with.

Tested through :func:`plt.cli.main` rather than through click's runner, for the same reason
``plt ingest`` is: the exit code is the contract with whatever schedules the capture, and a
scheduler that cannot tell "interrupted" from "broken" acts on the wrong one.

No database and no network are involved. A mirror is source data — it is deliberately
possible to take one before the schema exists.
"""

from __future__ import annotations

import json
import signal
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from plt.cli import main
from plt.config import Settings
from plt.pipeline import registry
from plt.pipeline.base import Candidate, SourceConnector, SourceUnavailableError
from plt.pipeline.runlog import LOG_DIR_NAME
from tests.conftest import build_settings
from tests.fakes import FakeConnector, FakeDocument, documents

#: Documents the fake source publishes during these tests, under CELEX-shaped identifiers.
EU_DOCS = [
    FakeDocument(
        source_id=f"62021CJ{index:04d}",
        modified_at=document.modified_at,
        text=document.text,
        language="en",
    )
    for index, document in enumerate(documents(3))
]


class MirrorCliConnector(FakeConnector):
    """The EU connector the CLI finds in the registry."""

    jurisdiction_code = "EU"
    name = "cli-mirror-fake"

    def __init__(self, settings: Settings | None = None) -> None:
        """Publish a fixed set of documents."""
        super().__init__(settings, docs=EU_DOCS)

    def enumerate_identifiers(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> Iterator[Candidate]:
        """State what the fake source holds, without walking anything.

        Args:
            since: Ignored; the fake's corpus is small enough to state whole.
            until: Ignored, for the same reason.

        Yields:
            One bare candidate per document.
        """
        del since, until
        for document in self.docs:
            yield Candidate(source_id=document.source_id, jurisdiction_code=self.jurisdiction_code)


class BrokenConnector(MirrorCliConnector):
    """A connector whose source is down."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Fail as soon as discovery is asked for anything."""
        super().__init__(settings)
        self.raise_on_discover = SourceUnavailableError("CELLAR is unreachable")


class InterruptedConnector(MirrorCliConnector):
    """A connector that raises ``SIGINT`` partway through the capture."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Interrupt the run on the second case."""
        super().__init__(settings)
        self.on_fetch = self._interrupt

    def _interrupt(self, source_id: str) -> None:
        """Raise ``SIGINT`` once the second case is reached.

        Args:
            source_id: The case about to be fetched.
        """
        if source_id == EU_DOCS[1].source_id:
            signal.raise_signal(signal.SIGINT)


class FailingConnector(MirrorCliConnector):
    """A connector one of whose cases cannot be served."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Refuse to serve the first case."""
        super().__init__(settings)
        self.fail_fetch = frozenset({EU_DOCS[0].source_id})


@pytest.fixture
def store_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the CLI at a temporary corpus store and a registry holding the fake."""
    root = tmp_path / "CaseLawStore"
    settings = build_settings(corpus_store_dir=root, pipeline_batch_size=2)
    monkeypatch.setattr("plt.cli.get_settings", lambda: settings)
    registry.reset_registry(MirrorCliConnector)
    try:
        yield root
    finally:
        registry.reset_registry()


@pytest.fixture(autouse=True)
def _no_signal_leak() -> Iterator[None]:
    """Restore the default ``SIGINT`` handler around every test in this module."""
    previous = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGINT, previous)


def case_folders(store_root: Path) -> list[str]:
    """Return the case folders in the EU store, leaving the run logs out.

    Args:
        store_root: Root of the case-law store.

    Returns:
        The folder names, sorted. ``logs/`` sits beside the cases and is not one of them.
    """
    return sorted(
        path.name
        for path in (store_root / "EU").iterdir()
        if path.is_dir() and path.name != LOG_DIR_NAME
    )


def test_a_capture_reports_success_and_leaves_the_corpus_on_disk(store_root: Path) -> None:
    code = main(["mirror", "--jurisdiction", "EU"])

    assert code == 0
    assert case_folders(store_root) == [document.source_id for document in EU_DOCS]
    assert (store_root / "EU" / "manifest.json").is_file()
    assert len(list((store_root / "EU" / "logs").iterdir())) == 1


def test_the_store_can_be_named_on_the_command_line(tmp_path: Path, store_root: Path) -> None:
    elsewhere = tmp_path / "Elsewhere"

    code = main(["mirror", "--jurisdiction", "EU", "--store", str(elsewhere)])

    assert code == 0
    assert (elsewhere / "EU" / EU_DOCS[0].source_id / "metadata.json").is_file()
    assert not store_root.exists()


def test_a_limited_capture_stops_where_it_was_told_to(store_root: Path) -> None:
    code = main(["mirror", "--jurisdiction", "EU", "--limit", "1"])

    assert code == 0
    assert len(case_folders(store_root)) == 1


def test_an_unreachable_source_exits_one(store_root: Path) -> None:
    assert store_root.parent.is_dir()
    registry.reset_registry(BrokenConnector)

    code = main(["mirror", "--jurisdiction", "EU"])

    assert code == 1


def test_an_interrupted_capture_exits_one_hundred_and_thirty(store_root: Path) -> None:
    registry.reset_registry(InterruptedConnector)

    code = main(["mirror", "--jurisdiction", "EU"])

    assert code == 130
    checkpoint = json.loads((store_root / "EU" / "_checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["last_source_id"] == EU_DOCS[1].source_id


def test_a_partial_capture_is_only_red_when_the_caller_asks(store_root: Path) -> None:
    registry.reset_registry(FailingConnector)

    assert main(["mirror", "--jurisdiction", "EU"]) == 0
    assert main(["mirror", "--jurisdiction", "EU", "--fail-on-partial"]) == 3
    assert (store_root / "EU" / "_failures.jsonl").is_file()


def test_naming_no_jurisdiction_is_a_usage_error(store_root: Path) -> None:
    assert store_root.parent.is_dir()
    assert main(["mirror"]) == 2


def test_a_jurisdiction_with_no_connector_exits_one(store_root: Path) -> None:
    assert store_root.parent.is_dir()

    assert main(["mirror", "--jurisdiction", "ZZ"]) == 1


def test_repair_fills_a_gap_without_walking_discovery(store_root: Path) -> None:
    main(["mirror", "--jurisdiction", "EU", "--limit", "1"])
    assert len(case_folders(store_root)) == 1

    code = main(["mirror", "--jurisdiction", "EU", "--repair"])

    assert code == 0
    assert case_folders(store_root) == [document.source_id for document in EU_DOCS]
    logs = sorted((store_root / "EU" / LOG_DIR_NAME).iterdir())
    assert logs[-1].read_text(encoding="utf-8").startswith("EU corpus repair - ")


def test_a_repair_cannot_be_asked_of_a_source_that_has_no_cheap_listing(
    store_root: Path,
) -> None:
    """Better a one-line refusal than the expensive walk the mode exists to avoid."""

    class Unlistable(MirrorCliConnector):
        """A connector that keeps the default listing, which is to have none."""

        enumerate_identifiers = SourceConnector.enumerate_identifiers

    registry.reset_registry(Unlistable)

    assert main(["mirror", "--jurisdiction", "EU", "--repair"]) == 1
    assert not case_folders(store_root)


def test_the_manifest_can_be_rebuilt_from_the_store_alone(
    store_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["mirror", "--jurisdiction", "EU"])
    manifest_path = store_root / "EU" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["contents"] = {"cases": 0, "resource_types": {}}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    code = main(["corpus-manifest", "--jurisdiction", "EU"])
    rebuilt = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert code == 0
    assert rebuilt["contents"]["cases"] == len(EU_DOCS)
    assert rebuilt["runs"] == manifest["runs"]
    assert f"{len(EU_DOCS)} cases on disk" in capsys.readouterr().out


def test_rebuilding_the_manifest_of_a_store_that_is_not_there_exits_one(
    store_root: Path,
) -> None:
    assert store_root.parent.is_dir()

    assert main(["corpus-manifest", "--jurisdiction", "EU"]) == 1


def test_a_store_that_cannot_be_opened_exits_one(tmp_path: Path, store_root: Path) -> None:
    assert store_root.parent.is_dir()
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "EU").write_text("something is already here", encoding="utf-8")

    # A traceback is the wrong report for a disk that is full, read-only or occupied.
    assert main(["mirror", "--jurisdiction", "EU", "--store", str(blocked)]) == 1
