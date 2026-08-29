"""Ingesting from the corpus store instead of from the source.

The whole design rests on one property: a payload read back off disk normalises into the case
the network run would have produced. So the central test here mirrors a corpus with a fake
connector, reads it back through :class:`StoredCorpusConnector`, and compares the two cases
field by field. If that ever stops holding, a backfill silently stops agreeing with the weekly
run, and no amount of testing the reader in isolation would show it.

The rest covers what the store can hand back that a source never would: a folder written before
metadata carried a file list, a folder whose payload has gone missing, and a folder that cannot
say which case it is.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from plt.config import Settings
from plt.pipeline.base import DocumentUnavailableError
from plt.pipeline.mirror import case_folder_name, mirror_jurisdiction
from plt.pipeline.store_source import StoredCorpusConnector, stored_corpus_connector
from tests.conftest import build_settings
from tests.fakes import EPOCH, FakeConnector, FakeDocument

# --------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------


class StoredFake(StoredCorpusConnector):
    """The adapter, wearing the fake connector's identity."""

    jurisdiction_code = "NL"
    name = "fake"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Point the corpus store at a temporary directory."""
    return build_settings(corpus_store_dir=tmp_path / "store")


def capture(settings: Settings, docs: list[FakeDocument]) -> None:
    """Mirror a fake source's documents to disk, which is what the reader then reads."""
    mirror_jurisdiction(
        "NL",
        settings=settings,
        connector=FakeConnector(settings, docs=docs),
    )


def reader(settings: Settings, docs: list[FakeDocument] | None = None) -> StoredFake:
    """Build the adapter over the mirrored corpus.

    The inner fake is given the same documents because :class:`~tests.fakes.FakeConnector`
    normalises by looking the identifier up in its own list rather than by reading the payload
    it was handed. Both shipped connectors do read the payload — that is the property this
    adapter depends on — so the fake is *more* forgiving than reality here, and a test that
    only compared normalised cases would prove nothing about what came off disk. The payloads
    are therefore compared directly, below.
    """
    return StoredFake(FakeConnector(settings, docs=docs or []), settings=settings)


# --------------------------------------------------------------------------------------
# The property the design rests on
# --------------------------------------------------------------------------------------


def test_a_stored_case_normalises_exactly_as_the_source_run_did(settings: Settings) -> None:
    """Reading a case off disk yields the same case as fetching it from the source."""
    docs = [
        FakeDocument(
            source_id="ECLI:NL:RBTEST:2026:1",
            title="Een uitspraak",
            abstract="Samenvatting",
            subject="Bestuursrecht",
            extra_texts=(("en", "An English version of the decision."),),
        )
    ]
    capture(settings, docs)

    source = FakeConnector(settings, docs=docs)
    raw_from_source = source.fetch(next(source.discover(None, None)))
    from_source = source.normalise(raw_from_source)

    stored = reader(settings, docs)
    try:
        candidate = next(stored.discover(None, None))
        raw_from_store = stored.fetch(candidate)
        from_store = stored.normalise(raw_from_store)
    finally:
        stored.close()

    # The payload is what normalisation reads, so this is the comparison that matters: the
    # bytes the source served and the bytes read back off disk have to be the same bytes.
    assert raw_from_store.payload == raw_from_source.payload
    assert raw_from_store.media_format == raw_from_source.media_format
    assert [
        (version["language"], version["payload"])
        for version in raw_from_store.source_metadata["manifestations"]
    ] == [
        (version["language"], version["payload"])
        for version in raw_from_source.source_metadata.get("manifestations", [])
    ]

    assert from_store.source_id == from_source.source_id
    assert from_store.title == from_source.title
    assert from_store.abstract == from_source.abstract
    assert from_store.subject == from_source.subject
    assert from_store.decision_date == from_source.decision_date
    assert from_store.language == from_source.language
    assert [document.full_text for document in from_store.documents] == [
        document.full_text for document in from_source.documents
    ]
    assert [document.language for document in from_store.documents] == [
        document.language for document in from_source.documents
    ]


def test_reading_the_store_sends_nothing(settings: Settings) -> None:
    """The adapter reports traffic, and the traffic is none.

    ``None`` would be the honest answer for a connector that does not measure; this one
    measures, and the answer is zero. The difference matters in a run log that has to show a
    backfill cost the source nothing.
    """
    capture(settings, [FakeDocument(source_id="ECLI:NL:RBTEST:2026:1")])
    stored = reader(settings)
    try:
        for candidate in stored.discover(None, None):
            stored.fetch(candidate)
        assert stored.traffic is not None
        assert stored.traffic.requests == 0
    finally:
        stored.close()


# --------------------------------------------------------------------------------------
# What the store can hand back that a source never would
# --------------------------------------------------------------------------------------


def test_a_folder_written_before_metadata_listed_its_files_is_still_read(
    settings: Settings,
) -> None:
    """The Dutch corpus arrived as ``raw_content.xml`` beside a metadata file with no list.

    Those folders are most of the corpus, so a reader that only understood the mirror's own
    layout would quietly skip 900,000 cases.
    """
    capture(settings, [FakeDocument(source_id="ECLI:NL:RBTEST:2026:1")])
    folder = Path(settings.corpus_store_dir) / "NL" / case_folder_name("ECLI:NL:RBTEST:2026:1")
    record = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    del record["files"]
    (folder / "metadata.json").write_text(json.dumps(record), encoding="utf-8")

    stored = reader(settings, [FakeDocument(source_id="ECLI:NL:RBTEST:2026:1")])
    try:
        candidate = next(stored.discover(None, None))
        raw = stored.fetch(candidate)
        case = stored.normalise(raw)
    finally:
        stored.close()
    assert raw.payload
    assert raw.media_format == "xml"
    assert case.source_id == "ECLI:NL:RBTEST:2026:1"
    assert case.documents


def test_a_case_whose_payload_has_gone_is_one_failure_not_a_dead_run(
    settings: Settings,
) -> None:
    """A hole in the corpus fails that document alone.

    The runner turns this into a counted failure and carries on, which is the only sane
    behaviour over a million cases.
    """
    capture(settings, [FakeDocument(source_id="ECLI:NL:RBTEST:2026:1")])
    folder = Path(settings.corpus_store_dir) / "NL" / case_folder_name("ECLI:NL:RBTEST:2026:1")
    for payload in folder.glob("raw_content.*"):
        payload.unlink()

    stored = reader(settings)
    try:
        candidate = next(stored.discover(None, None))
        with pytest.raises(DocumentUnavailableError):
            stored.fetch(candidate)
    finally:
        stored.close()


def test_a_folder_that_names_no_case_is_skipped(settings: Settings) -> None:
    """Discovery walks past a folder whose metadata carries no identifier."""
    capture(settings, [FakeDocument(source_id="ECLI:NL:RBTEST:2026:1")])
    root = Path(settings.corpus_store_dir) / "NL"
    broken = root / "not-a-case"
    broken.mkdir()
    (broken / "metadata.json").write_text("{}", encoding="utf-8")

    stored = reader(settings)
    try:
        found = [candidate.source_id for candidate in stored.discover(None, None)]
    finally:
        stored.close()
    assert found == ["ECLI:NL:RBTEST:2026:1"]


def test_the_window_narrows_what_is_read(settings: Settings) -> None:
    """``--since`` and ``--until`` filter on the modification instant the store recorded."""
    docs = [
        FakeDocument(
            source_id=f"ECLI:NL:RBTEST:2026:{index}",
            modified_at=EPOCH + timedelta(days=index),
        )
        for index in range(4)
    ]
    capture(settings, docs)

    stored = reader(settings)
    try:
        found = sorted(
            candidate.source_id
            for candidate in stored.discover(EPOCH + timedelta(days=1), EPOCH + timedelta(days=2))
        )
    finally:
        stored.close()
    assert found == ["ECLI:NL:RBTEST:2026:1", "ECLI:NL:RBTEST:2026:2"]


def test_a_case_with_no_recorded_instant_is_still_read(settings: Settings) -> None:
    """A corpus is not a feed: a case that cannot be dated is ingested rather than dropped."""
    capture(settings, [FakeDocument(source_id="ECLI:NL:RBTEST:2026:1")])
    folder = Path(settings.corpus_store_dir) / "NL" / case_folder_name("ECLI:NL:RBTEST:2026:1")
    record = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    record.pop("source_modified_at", None)
    record.pop("modified", None)
    (folder / "metadata.json").write_text(json.dumps(record), encoding="utf-8")

    stored = reader(settings)
    try:
        found = [candidate.source_id for candidate in stored.discover(EPOCH, None)]
    finally:
        stored.close()
    assert found == ["ECLI:NL:RBTEST:2026:1"]


# --------------------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------------------


def test_the_adapter_wears_the_wrapped_connectors_identity(settings: Settings) -> None:
    """A store run writes the same connector name, so it advances the same checkpoint.

    Renaming the source for a run that read the mirror would strand the weekly run's
    position and file the same cases under two connectors.
    """
    built = stored_corpus_connector("NL", settings=settings)
    try:
        assert built.jurisdiction_code == "NL"
        assert built.name == "rechtspraak"
        assert built.store_path == Path(settings.corpus_store_dir) / "NL"
    finally:
        built.close()
