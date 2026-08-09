"""The corpus mirror: what ends up on disk, and what a resumed capture does.

Everything here drives a fake connector against a temporary directory — no network
(``CONTRIBUTING.md`` section 4) — and asserts on the files, because the files are the
deliverable. A mirror that reports success and leaves an unreadable folder behind has failed
at the one thing it exists for.

The interruption test raises a real ``SIGINT`` mid-run rather than setting the flag by hand:
the requirement is that the signal is *trapped*, and a test that set the flag itself would
pass even if the handler were never installed.
"""

from __future__ import annotations

import json
import signal
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from plt.config import Settings
from plt.db.models import DocumentType, IngestStatus
from plt.pipeline.base import (
    NormalisedCase,
    NormalisedCourt,
    NormalisedDocument,
    RawDocument,
    SourceUnavailableError,
)
from plt.pipeline.checkpoint import Checkpoint
from plt.pipeline.mirror import (
    CorpusStore,
    CorpusStoreError,
    MirrorReport,
    case_folder_name,
    mirror_jurisdiction,
    rebuild_manifest,
)
from tests.conftest import build_settings
from tests.fakes import EPOCH, FakeConnector, FakeDocument, documents

# --------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------


class MirrorConnector(FakeConnector):
    """A fake whose language versions carry payloads of their own.

    :class:`~tests.fakes.FakeConnector` gives every version the same payload, which is right
    for the ingestion tests and wrong here: the EU connector returns one *distinct* body per
    language, and whether the mirror writes each of them to its own file is exactly what is
    under test.
    """

    jurisdiction_code = "EU"
    name = "eurlex"

    @staticmethod
    def resource_type(source_id: str) -> str:
        """Return the kind of record a case is, as CELLAR would state it.

        Args:
            source_id: The case's CELEX number.

        Returns:
            ``OPIN_AG`` for every third case and ``JUDG`` for the rest, so that a store built
            from these documents has a breakdown to observe rather than one uniform type.
        """
        return "OPIN_AG" if source_id.endswith(("2", "5", "8")) else "JUDG"

    def normalise(self, raw: RawDocument) -> NormalisedCase:
        """Map the payload onto the schema, one document per language version.

        Args:
            raw: The payload returned by ``fetch``.

        Returns:
            The case, carrying the source record and one distinct body per extra language.
        """
        case = super().normalise(raw)
        document = self._document(raw.source_id)
        versions = [
            NormalisedDocument(
                doc_type=DocumentType.JUDGMENT,
                language=document.language,
                full_text=document.text,
                raw_payload=raw.payload,
                media_format="xml",
                source_url=raw.source_url,
                source_metadata={"content_type": "application/xml"},
            ),
            *(
                NormalisedDocument(
                    doc_type=DocumentType.JUDGMENT,
                    language=language,
                    full_text=text,
                    raw_payload=f"<html lang='{language}'>{text}</html>",
                    media_format="xhtml",
                    source_url=f"https://example.invalid/{raw.source_id}/{language}",
                    source_metadata={"content_type": "application/xhtml+xml"},
                )
                for language, text in document.extra_texts
            ),
        ]
        return NormalisedCase(
            source_id=case.source_id,
            jurisdiction_code=self.jurisdiction_code,
            source_system="cellar",
            title=case.title,
            subject=case.subject,
            decision_date=case.decision_date,
            language=case.language,
            case_numbers=case.case_numbers,
            source_url=case.source_url,
            court=NormalisedCourt(source_identifier="curia", name="Court of Justice"),
            documents=tuple(versions),
            source_metadata={
                "celex": case.source_id,
                "eurovoc_descriptors": ["pesticide"],
                "resource_type": self.resource_type(case.source_id),
            },
        )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return settings whose corpus store is a temporary directory."""
    return build_settings(corpus_store_dir=tmp_path / "CaseLawStore", pipeline_batch_size=2)


def eu_documents(count: int = 3) -> list[FakeDocument]:
    """Build CELEX-shaped documents, one minute apart, oldest first.

    Args:
        count: How many to build.

    Returns:
        The documents, in the order discovery yields them.
    """
    built = documents(count)
    return [
        FakeDocument(
            source_id=f"6202{index % 5}CJ{index:04d}",
            modified_at=document.modified_at,
            text=document.text,
            title=f"Judgment {index}",
            language="en",
            extra_texts=(("fr", f"Arrêt numéro {index}"),),
        )
        for index, document in enumerate(built)
    ]


def run(
    settings: Settings,
    docs: Sequence[FakeDocument],
    **overrides: object,
) -> tuple[MirrorReport, MirrorConnector]:
    """Mirror a fake source into the configured store.

    Args:
        settings: Settings whose ``corpus_store_dir`` is a temporary directory.
        docs: The documents the fake source holds.
        **overrides: Keyword arguments for the connector, e.g. ``fail_fetch``.

    Returns:
        The run's report and the connector it drove, so a test can assert on what was fetched.
    """
    connector = MirrorConnector(settings, docs=docs, **overrides)  # type: ignore[arg-type]
    report = mirror_jurisdiction("EU", settings=settings, connector=connector)
    return report, connector


def store_for(settings: Settings) -> CorpusStore:
    """Return the store the fixtures write into.

    Args:
        settings: The test settings.

    Returns:
        The EU store under the temporary root.
    """
    return CorpusStore(settings.corpus_store_dir, "EU")


def read_json(path: Path) -> dict[str, object]:
    """Read a JSON file the mirror wrote.

    Args:
        path: The file.

    Returns:
        The parsed object.
    """
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


# --------------------------------------------------------------------------------------
# Folder names
# --------------------------------------------------------------------------------------


class TestCaseFolderName:
    """A source identifier becomes exactly one predictable, safe path segment."""

    def test_an_ecli_takes_the_shape_the_dutch_store_already_uses(self) -> None:
        assert case_folder_name("ECLI:NL:CBB:1994:ZG1226") == "ECLI_NL_CBB_1994_ZG1226"

    def test_a_celex_number_is_left_alone(self) -> None:
        assert case_folder_name("62017CJ0616") == "62017CJ0616"

    def test_a_parenthesised_corrigendum_keeps_its_suffix(self) -> None:
        assert case_folder_name("62021TO0601(01)") == "62021TO0601(01)"

    @pytest.mark.parametrize("identifier", ["a/b", "a\\b", "C:evil", "x y"])
    def test_a_separator_cannot_survive_into_a_path(self, identifier: str) -> None:
        name = case_folder_name(identifier)

        assert "/" not in name
        assert "\\" not in name
        assert ":" not in name
        assert Path(name).name == name

    @pytest.mark.parametrize(
        "identifier",
        [
            "../../etc/passwd",
            "..\\..\\windows",
            "",
            "   ",
            "." * 3,
            "x" * 200,
            "NUL",
            "com1.txt",
            "manifest.json",
            "_checkpoint.json",
        ],
    )
    def test_an_unusable_identifier_is_refused_rather_than_repaired(self, identifier: str) -> None:
        with pytest.raises(CorpusStoreError):
            case_folder_name(identifier)


# --------------------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------------------


class TestCorpusStore:
    """The layout: one directory per jurisdiction, one folder per case."""

    def test_the_jurisdiction_gets_a_directory_of_its_own(self, tmp_path: Path) -> None:
        store = CorpusStore(tmp_path, "eu")
        store.prepare()

        assert store.path == tmp_path / "EU"
        assert store.path.is_dir()

    def test_a_jurisdiction_code_that_is_not_one_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(CorpusStoreError):
            CorpusStore(tmp_path, "../EU")

    def test_an_absent_case_is_not_held(self, tmp_path: Path) -> None:
        store = CorpusStore(tmp_path, "EU")
        store.prepare()

        assert store.holds("62017CJ0616") is False

    def test_a_checkpoint_survives_a_round_trip(self, tmp_path: Path) -> None:
        store = CorpusStore(tmp_path, "EU")
        store.prepare()
        position = Checkpoint(
            connector="eurlex",
            jurisdiction_code="EU",
            last_modified_seen=datetime(2026, 5, 1, 9, 30, tzinfo=UTC),
            last_cursor="window#100",
            last_source_id="62017CJ0616",
        )

        store.write_checkpoint(position)
        restored = store.read_checkpoint("eurlex")

        assert restored is not None
        assert restored.last_modified_seen == position.last_modified_seen
        assert restored.last_cursor == "window#100"
        assert restored.last_source_id == "62017CJ0616"

    def test_another_connector_does_not_resume_from_this_one_s_cursor(self, tmp_path: Path) -> None:
        store = CorpusStore(tmp_path, "EU")
        store.prepare()
        store.write_checkpoint(Checkpoint(connector="eurlex", jurisdiction_code="EU"))

        assert store.read_checkpoint("something-else") is None

    def test_an_unreadable_checkpoint_starts_the_window_again(self, tmp_path: Path) -> None:
        store = CorpusStore(tmp_path, "EU")
        store.prepare()
        (store.path / "_checkpoint.json").write_text("{not json", encoding="utf-8")

        assert store.read_checkpoint("eurlex") is None


# --------------------------------------------------------------------------------------
# A capture
# --------------------------------------------------------------------------------------


class TestMirroring:
    """What a completed capture leaves on disk."""

    def test_every_case_gets_a_folder_holding_its_payloads_verbatim(
        self, settings: Settings
    ) -> None:
        docs = eu_documents(3)
        report, _ = run(settings, docs)
        store = store_for(settings)

        assert report.status is IngestStatus.SUCCESS
        assert report.counters.mirrored == 3
        for document in docs:
            folder = store.path / document.source_id
            assert (folder / "metadata.json").is_file()
            assert (folder / "raw_content.xml").read_text(encoding="utf-8") == (
                f"<uitspraak>{document.text}</uitspraak>"
            )
            assert (folder / "fulltext.fr.xhtml").read_text(encoding="utf-8") == (
                f"<html lang='fr'>{document.extra_texts[0][1]}</html>"
            )

    def test_the_source_record_is_not_written_twice(self, settings: Settings) -> None:
        run(settings, eu_documents(1))
        folder = store_for(settings).path / eu_documents(1)[0].source_id

        # The English version's payload *is* the source record, so it is stored once, under
        # the name the Dutch store already uses, rather than duplicated as fulltext.en.xml.
        assert sorted(path.name for path in folder.iterdir()) == [
            "fulltext.fr.xhtml",
            "metadata.json",
            "raw_content.xml",
        ]

    def test_a_case_records_when_it_was_fetched_and_from_where(self, settings: Settings) -> None:
        docs = eu_documents(1)
        run(settings, docs)
        record = read_json(store_for(settings).path / docs[0].source_id / "metadata.json")

        assert record["identifier"] == docs[0].source_id
        assert record["jurisdiction"] == "EU"
        assert record["connector"] == "eurlex"
        assert record["source_system"] == "cellar"
        assert record["normalised"] is True
        assert record["fetched_from"] == f"https://example.invalid/{docs[0].source_id}"
        assert isinstance(record["fetched_at"], str)
        assert datetime.fromisoformat(str(record["fetched_at"])).tzinfo is not None
        assert record["source_modified_at"] == docs[0].modified_at.isoformat()

    def test_the_file_list_describes_what_is_beside_it(self, settings: Settings) -> None:
        docs = eu_documents(1)
        run(settings, docs)
        folder = store_for(settings).path / docs[0].source_id
        record = read_json(folder / "metadata.json")
        files = record["files"]

        assert isinstance(files, list)
        by_name = {str(entry["name"]): entry for entry in files}
        assert by_name["raw_content.xml"]["role"] == "source_record"
        assert by_name["fulltext.fr.xhtml"]["role"] == "full_text"
        assert by_name["fulltext.fr.xhtml"]["language"] == "fr"
        for name, entry in by_name.items():
            assert entry["size_bytes"] == (folder / name).stat().st_size

    def test_the_case_is_only_complete_once_its_metadata_is_written(
        self, settings: Settings
    ) -> None:
        docs = eu_documents(1)
        run(settings, docs)
        store = store_for(settings)
        (store.path / docs[0].source_id / "metadata.json").unlink()

        # The payloads are still there, but nothing claims the case is finished, so the next
        # run fetches it again rather than counting a half-written folder as done.
        assert store.holds(docs[0].source_id) is False

    def test_a_folder_holding_another_case_is_never_overwritten(self, settings: Settings) -> None:
        store = store_for(settings)
        store.prepare()
        folder = store.path / eu_documents(1)[0].source_id
        folder.mkdir()
        (folder / "metadata.json").write_text(
            json.dumps({"identifier": "a-different-case"}), encoding="utf-8"
        )

        report, _ = run(settings, eu_documents(1))

        assert report.status is IngestStatus.PARTIAL
        assert report.counters.errors == 1
        assert json.loads((folder / "metadata.json").read_text(encoding="utf-8")) == {
            "identifier": "a-different-case"
        }

    def test_a_case_that_cannot_be_normalised_still_keeps_its_bytes(
        self, settings: Settings
    ) -> None:
        docs = eu_documents(1)
        report, _ = run(settings, docs, fail_normalise=frozenset({docs[0].source_id}))
        folder = store_for(settings).path / docs[0].source_id

        assert report.counters.mirrored == 1
        assert (folder / "raw_content.xml").is_file()
        record = read_json(folder / "metadata.json")
        assert record["normalised"] is False
        assert record["identifier"] == docs[0].source_id


class TestResuming:
    """A capture of a hundred thousand cases will be interrupted; it must pick up."""

    def test_a_case_already_held_costs_no_request(self, settings: Settings) -> None:
        docs = eu_documents(3)
        run(settings, docs)

        # No window is given, so the second run resumes from the stored position. What it
        # re-offers is the case the checkpoint sits on, and that one is already on disk.
        report, connector = run(settings, docs)

        assert report.counters.mirrored == 0
        assert report.counters.skipped == report.counters.discovered >= 1
        assert connector.fetched == []
        assert store_for(settings).count_cases() == 3

    def test_a_whole_window_offered_again_costs_no_request_either(self, settings: Settings) -> None:
        docs = eu_documents(3)
        run(settings, docs)

        connector = MirrorConnector(settings, docs=docs)
        report = mirror_jurisdiction("EU", EPOCH, settings=settings, connector=connector)

        assert report.counters.skipped == 3
        assert report.counters.mirrored == 0
        assert connector.fetched == []

    def test_sigint_finishes_the_case_in_flight_and_records_it(self, settings: Settings) -> None:
        docs = eu_documents(5)
        interrupt_at = docs[2].source_id

        def interrupt(source_id: str) -> None:
            if source_id == interrupt_at:
                signal.raise_signal(signal.SIGINT)

        report, connector = run(settings, docs, on_fetch=interrupt)
        store = store_for(settings)

        assert report.status is IngestStatus.INTERRUPTED
        assert connector.fetched == [document.source_id for document in docs[:3]]
        assert store.count_cases() == 3
        assert store.holds(interrupt_at) is True
        position = store.read_checkpoint("eurlex")
        assert position is not None
        assert position.last_source_id == interrupt_at
        assert position.last_modified_seen == docs[2].modified_at

    def test_the_next_run_starts_where_the_last_one_stopped(self, settings: Settings) -> None:
        docs = eu_documents(5)

        def interrupt(source_id: str) -> None:
            if source_id == docs[2].source_id:
                signal.raise_signal(signal.SIGINT)

        run(settings, docs, on_fetch=interrupt)
        report, resumed = run(settings, docs)

        assert report.status is IngestStatus.SUCCESS
        # Discovery resumes from the checkpointed instant, and the one case it re-offers is
        # already on disk, so only the untouched tail is fetched.
        assert resumed.discovered == [document.source_id for document in docs[2:]]
        assert resumed.fetched == [document.source_id for document in docs[3:]]
        assert store_for(settings).count_cases() == 5

    def test_the_position_is_written_before_the_run_ends(self, settings: Settings) -> None:
        docs = eu_documents(5)

        def die(source_id: str) -> None:
            if source_id == docs[4].source_id:
                message = "the source went away"
                raise SourceUnavailableError(message)

        report, _ = run(settings, docs, on_fetch=die)
        position = store_for(settings).read_checkpoint("eurlex")

        assert report.status is IngestStatus.FAILED
        assert report.error_message is not None
        # The four cases already on disk are not thrown away with the run: unlike an
        # ingestion transaction, a written file cannot be rolled back.
        assert position is not None
        assert position.last_modified_seen == docs[3].modified_at


class TestFailures:
    """A case that will not come down is recorded, and holds the window open."""

    def test_a_failed_case_holds_the_checkpoint_back(self, settings: Settings) -> None:
        docs = eu_documents(4)
        report, _ = run(settings, docs, fail_fetch=frozenset({docs[1].source_id}))
        position = store_for(settings).read_checkpoint("eurlex")

        assert report.status is IngestStatus.PARTIAL
        assert report.counters.errors == 1
        assert report.counters.mirrored == 3
        assert position is not None
        # Nothing after the failure is considered done, so the window is offered again.
        assert position.last_modified_seen == docs[0].modified_at

    def test_a_failure_says_which_case_and_why(self, settings: Settings) -> None:
        docs = eu_documents(2)
        run(settings, docs, fail_fetch=frozenset({docs[0].source_id}))
        recorded = (store_for(settings).path / "_failures.jsonl").read_text(encoding="utf-8")
        entries = [json.loads(line) for line in recorded.splitlines() if line.strip()]

        assert [entry["identifier"] for entry in entries] == [docs[0].source_id]
        assert "DocumentUnavailableError" in entries[0]["reason"]
        assert datetime.fromisoformat(entries[0]["at"]).tzinfo is not None

    def test_the_failed_case_is_offered_again_next_run(self, settings: Settings) -> None:
        docs = eu_documents(3)
        run(settings, docs, fail_fetch=frozenset({docs[0].source_id}))

        report, connector = run(settings, docs)

        assert report.status is IngestStatus.SUCCESS
        assert docs[0].source_id in connector.fetched
        assert store_for(settings).count_cases() == 3


class TestManifest:
    """What the corpus is, and when it was taken."""

    def test_the_manifest_states_the_capture_window_and_the_totals(
        self, settings: Settings
    ) -> None:
        until = EPOCH.replace(year=2027)
        connector = MirrorConnector(settings, docs=eu_documents(3))
        report = mirror_jurisdiction("EU", None, until, settings=settings, connector=connector)
        manifest = read_json(store_for(settings).path / "manifest.json")
        capture = manifest["capture"]
        totals = manifest["totals"]

        assert manifest["jurisdiction"] == "EU"
        assert manifest["connector"] == "eurlex"
        assert manifest["layout_version"] == 2
        assert isinstance(capture, dict)
        assert capture["window_until"] == until.isoformat()
        assert capture["status"] == IngestStatus.SUCCESS.value
        assert datetime.fromisoformat(str(capture["started_at"])).tzinfo is not None
        assert isinstance(totals, dict)
        assert totals["bytes_written"] == report.counters.bytes_written
        assert totals["runs"] == 1
        # The corpus's own figures live in one place, and it is the one counted from disk.
        assert "cases" not in totals

    def test_the_manifest_describes_the_corpus_by_counting_it(self, settings: Settings) -> None:
        run(settings, eu_documents(3))
        contents = read_json(store_for(settings).path / "manifest.json")["contents"]

        assert isinstance(contents, dict)
        assert contents["cases"] == 3
        # Two judgments and an opinion were stored, and that is what the store says it holds.
        assert contents["resource_types"] == {"JUDG": 2, "OPIN_AG": 1}
        assert contents["languages"] == {"en": 3, "fr": 3}
        assert contents["payload_bytes"] > 0
        assert contents["unreadable"] == 0
        assert datetime.fromisoformat(str(contents["observed_at"])).tzinfo is not None
        assert isinstance(contents["fetched_at"], dict)
        assert contents["fetched_at"]["earliest"] is not None
        assert contents["fetched_at"]["latest"] is not None

    def test_the_scope_is_what_the_store_holds_rather_than_what_was_configured(
        self, settings: Settings
    ) -> None:
        # The defect this exists for: a run launched with the narrow default against a store
        # captured under a wider one used to leave the store declaring the narrow list.
        narrow = build_settings(
            corpus_store_dir=settings.corpus_store_dir,
            eurlex_resource_types=["JUDG"],
        )
        run(narrow, eu_documents(3))
        manifest = read_json(store_for(settings).path / "manifest.json")
        contents, configuration = manifest["contents"], manifest["configuration"]

        assert isinstance(contents, dict)
        assert isinstance(configuration, dict)
        assert list(contents["resource_types"]) == ["JUDG", "OPIN_AG"]
        assert configuration["settings"]["eurlex_resource_types"] == ["JUDG"]

    def test_the_manifest_records_the_configuration_the_capture_was_taken_under(
        self, settings: Settings
    ) -> None:
        report, _ = run(settings, eu_documents(1))
        configuration = read_json(store_for(settings).path / "manifest.json")["configuration"]

        assert isinstance(configuration, dict)
        # Labelled as the run's, and attributed to a named run, so it cannot be read as a
        # description of the corpus.
        assert "contents" in str(configuration["note"])
        assert configuration["recorded_by"] == {
            "run_started_at": report.started_at.isoformat(),
            "mode": "mirror",
            "status": IngestStatus.SUCCESS.value,
        }
        source = configuration["settings"]
        # Every setting the connector reads is named after it, so the manifest can state what
        # the run was launched with without this module knowing which jurisdiction it is.
        assert source["eurlex_sparql_url"] == settings.eurlex_sparql_url
        assert source["eurlex_languages"] == settings.eurlex_languages
        assert source["requests_per_second"] == settings.http_requests_per_second
        assert "PLT/" in str(source["user_agent"])

    def test_a_failed_run_does_not_redescribe_how_the_corpus_was_captured(
        self, settings: Settings
    ) -> None:
        run(settings, eu_documents(3))
        first = read_json(store_for(settings).path / "manifest.json")["configuration"]
        narrow = build_settings(
            corpus_store_dir=settings.corpus_store_dir,
            eurlex_resource_types=["JUDG"],
        )
        connector = MirrorConnector(
            narrow, docs=eu_documents(4), raise_on_discover=SourceUnavailableError("gone")
        )
        report = mirror_jurisdiction("EU", settings=narrow, connector=connector)
        second = read_json(store_for(settings).path / "manifest.json")

        assert report.status is IngestStatus.FAILED
        assert second["configuration"] == first
        # What the failed run did is still recorded, where a run's own record belongs.
        assert isinstance(second["runs"], list)
        assert second["runs"][-1]["status"] == IngestStatus.FAILED.value

    def test_a_manifest_written_by_the_older_layout_is_migrated_rather_than_dropped(
        self, settings: Settings
    ) -> None:
        run(settings, eu_documents(1))
        store = store_for(settings)
        legacy = {
            **read_json(store.path / "manifest.json"),
            "layout_version": 1,
            "source": {"eurlex_resource_types": ["JUDG", "ORDER"]},
        }
        del legacy["configuration"]
        store.write_manifest(legacy)
        connector = MirrorConnector(
            settings, docs=eu_documents(2), raise_on_discover=SourceUnavailableError("gone")
        )
        mirror_jurisdiction("EU", settings=settings, connector=connector)
        configuration = read_json(store.path / "manifest.json")["configuration"]

        assert isinstance(configuration, dict)
        assert configuration["settings"] == {"eurlex_resource_types": ["JUDG", "ORDER"]}
        # The old layout did not say which run wrote it, and nothing here invents an answer.
        assert configuration["recorded_by"] is None

    def test_a_resumed_capture_keeps_the_instant_it_started(self, settings: Settings) -> None:
        docs = eu_documents(4)

        def interrupt(source_id: str) -> None:
            if source_id == docs[1].source_id:
                signal.raise_signal(signal.SIGINT)

        run(settings, docs, on_fetch=interrupt)
        first = read_json(store_for(settings).path / "manifest.json")
        run(settings, docs)
        second = read_json(store_for(settings).path / "manifest.json")

        assert isinstance(first["capture"], dict)
        assert isinstance(second["capture"], dict)
        assert second["capture"]["started_at"] == first["capture"]["started_at"]
        assert second["capture"]["updated_at"] != first["capture"]["updated_at"]
        assert isinstance(second["totals"], dict)
        assert second["totals"]["runs"] == 2
        assert isinstance(second["runs"], list)
        assert len(second["runs"]) == 2


class TestSurvey:
    """The store describes itself, and the description is a count rather than a claim."""

    def test_an_empty_store_says_so_rather_than_failing(self, tmp_path: Path) -> None:
        store = CorpusStore(tmp_path, "EU")
        store.prepare()
        survey = store.survey()

        assert survey.cases == 0
        assert survey.resource_types == {}
        assert survey.earliest_decision is None
        assert survey.observed_at.tzinfo is not None

    def test_a_store_that_is_not_there_is_described_as_empty(self, tmp_path: Path) -> None:
        survey = CorpusStore(tmp_path / "nothing", "EU").survey()

        assert survey.cases == 0

    def test_the_span_of_the_corpus_is_read_off_the_cases(self, settings: Settings) -> None:
        run(settings, eu_documents(4))
        survey = store_for(settings).survey()

        assert survey.cases == 4
        assert survey.earliest_decision is not None
        assert survey.latest_decision is not None
        assert survey.earliest_decision <= survey.latest_decision
        assert survey.first_fetched_at is not None
        assert survey.last_fetched_at is not None

    def test_a_case_whose_metadata_cannot_be_read_is_counted_and_not_described(
        self, settings: Settings
    ) -> None:
        run(settings, eu_documents(3))
        store = store_for(settings)
        damaged = store.case_dir(eu_documents(3)[0].source_id) / "metadata.json"
        damaged.write_text("{ not json", encoding="utf-8")
        survey = store.survey()

        assert survey.cases == 3
        assert survey.unreadable == 1
        # The payloads are still on disk, so the case is held; it just describes nothing.
        assert sum(survey.resource_types.values()) == 2

    def test_a_breakdown_is_bounded_by_a_setting_rather_than_by_the_corpus(
        self, tmp_path: Path
    ) -> None:
        store = CorpusStore(tmp_path, "EU")
        store.prepare()
        for index in range(300):
            case = store.path / f"case{index:04d}"
            case.mkdir()
            (case / "metadata.json").write_text(
                json.dumps(
                    {
                        "identifier": f"case{index}",
                        "source_metadata": {"resource_type": f"TYPE{index:04d}"},
                    }
                ),
                encoding="utf-8",
            )
        survey = store.survey()

        assert survey.cases == 300
        assert sum(survey.resource_types.values()) == 300
        # Whatever a store turns out to hold, what the walk accumulates has a ceiling.
        assert len(survey.resource_types) < 300
        assert survey.resource_types["(other)"] > 0


class TestRebuildManifest:
    """A store's description of itself can be re-derived from the store, on its own."""

    def test_the_contents_are_re_observed_and_the_rest_carried_forward(
        self, settings: Settings
    ) -> None:
        run(settings, eu_documents(3))
        store = store_for(settings)
        before = read_json(store.path / "manifest.json")
        stale = {**before, "contents": {"cases": 1, "resource_types": {"JUDG": 1}}}
        store.write_manifest(stale)

        path, survey = rebuild_manifest("EU", settings=settings)
        after = read_json(path)

        contents = after["contents"]
        assert path == store.path / "manifest.json"
        assert survey.cases == 3
        assert isinstance(contents, dict)
        assert contents["cases"] == 3
        assert contents["resource_types"] == {"JUDG": 2, "OPIN_AG": 1}
        # Nothing a run recorded is invented, dropped or re-stated by a walk of the disk.
        assert after["capture"] == before["capture"]
        assert after["configuration"] == before["configuration"]
        assert after["runs"] == before["runs"]
        assert after["totals"] == before["totals"]
        assert after["connector"] == "eurlex"
        assert after["layout_version"] == 2

    def test_it_corrects_a_manifest_the_older_layout_left_behind(self, settings: Settings) -> None:
        run(settings, eu_documents(3))
        store = store_for(settings)
        legacy = {
            "layout_version": 1,
            "jurisdiction": "EU",
            "connector": "eurlex",
            "capture": {"window_since": None},
            "source": {"eurlex_resource_types": ["JUDG"]},
            "totals": {"cases": 99, "bytes_written": 12, "runs": 2},
            "runs": [],
        }
        store.write_manifest(legacy)

        _, survey = rebuild_manifest("EU", settings=settings, store_root=settings.corpus_store_dir)
        after = read_json(store.path / "manifest.json")

        contents, configuration = after["contents"], after["configuration"]
        assert survey.resource_types == {"JUDG": 2, "OPIN_AG": 1}
        assert isinstance(contents, dict)
        assert isinstance(configuration, dict)
        assert contents["cases"] == 3
        # The narrow declaration is kept, as a fact about a run, and no longer as scope.
        assert configuration["settings"] == {"eurlex_resource_types": ["JUDG"]}
        assert after["totals"] == {"bytes_written": 12, "runs": 2}
        assert "source" not in after

    def test_a_store_that_does_not_exist_is_refused(self, settings: Settings) -> None:
        with pytest.raises(CorpusStoreError):
            rebuild_manifest("EU", settings=settings)


class TestLimit:
    """A rehearsal fetches a handful and stops."""

    def test_a_limited_run_stops_once_it_has_that_many(self, settings: Settings) -> None:
        connector = MirrorConnector(settings, docs=eu_documents(10))
        report = mirror_jurisdiction("EU", settings=settings, connector=connector, limit=2)

        assert report.counters.mirrored == 2
        assert store_for(settings).count_cases() == 2
        assert report.status is IngestStatus.SUCCESS


@pytest.fixture(autouse=True)
def _no_signal_leak() -> Iterator[None]:
    """Restore the default ``SIGINT`` handler around every test in this module.

    The mirror installs its own handler and restores the previous one on the way out; this
    guards the rest of the suite against a test that fails before it gets there.
    """
    previous = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGINT, previous)
