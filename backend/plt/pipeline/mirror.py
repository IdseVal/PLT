"""The corpus mirror: one jurisdiction's source payloads, on disk, verbatim.

A mirror is not an ingestion. It stores what a source served, unfiltered and unclassified,
so that everything downstream can be re-run over an **identical** corpus. That is what
``docs/CORE_DOCUMENT.md`` section 2.8 asks for and what a live endpoint cannot give: two
keyword lists scored against CELLAR a week apart are not comparable, because the repository
moved underneath them. Scored against a mirror they are, and the difference between them is
the list rather than the day.

The shape on disk is one directory per jurisdiction, one folder per case::

    <corpus_store_dir>/
        NL/
            ECLI_NL_CBB_1994_ZG1226/
                metadata.json
                raw_content.xml
        EU/
            62017CJ0616/
                metadata.json          index + provenance
                raw_content.xml        the source record, verbatim
                fulltext.en.xhtml      one per language version, verbatim
            manifest.json              what the corpus is, and when it was taken
            _checkpoint.json           where the next capture resumes
            _repair_checkpoint.json    how far the last repair read the identifier listing
            _failures.jsonl            the cases that did not come down, and why
            logs/                      one readable record per run

The Dutch half of that tree already exists, so its names are taken as given: a folder per
case, ``metadata.json`` beside ``raw_content.xml``. Nothing here is EU-specific — the mirror
drives a :class:`~plt.pipeline.base.SourceConnector` through the registry and writes what it
returns, so a third jurisdiction is mirrored by the connector it is onboarded with and no
change to this module.

Five properties, each a requirement rather than a detail.

**Verbatim.** ``RawDocument.payload`` becomes ``raw_content.<format>`` and every further
payload the case carries — a full-text manifestation in another language — becomes
``fulltext.<language>.<format>``. Both are written exactly as the connector received them
(``docs/architecture.md`` rule 6, core document 2.2), so re-classification never needs a
re-fetch. The payloads cross the connector as text, so what is preserved is the source's
characters rather than its bytes; the declared content type is recorded beside them.

**Polite.** Every request goes through the connector, which fetches through
:class:`plt.pipeline.http.PoliteClient`: the configured rate, exponential backoff with
jitter, and ``Retry-After`` obeyed exactly as sent. These are the Publications Office's
endpoints and the project needs them weekly for years.

**Resumable.** ``metadata.json`` is written *last*, so its presence is what marks a case
complete; a case already on disk is skipped without a request. The position is kept in
``_checkpoint.json`` — the same :class:`~plt.pipeline.checkpoint.Checkpoint` value the
ingestion pipeline uses, in a file rather than a table — and written every batch, so a hard
kill costs a batch rather than a day. It is deliberately **not** the ``ingest_checkpoint``
row: a mirror pass that advanced the ingestion position would make the pipeline skip cases
it never ingested.

**Interruptible.** ``SIGINT`` sets a flag through
:class:`~plt.utils.shutdown.StopRequest`; the case in flight is finished and written, the
checkpoint is written, and the run ends as ``interrupted`` (architecture rule 2). A signal
arriving mid-backoff is noticed when that request resolves, so the wait can be as long as
the retry budget; a second signal falls through to the default handler.

**Accountable.** Each case records when it was fetched and from what URL; the corpus records
what it holds, its capture window and the configuration of the run that took it in
``manifest.json``, and every failure in ``_failures.jsonl``. A published methodology has to be
able to say what the corpus is and when it was taken, and an absence nobody can audit is the
expensive kind. On top of those machine records, every run - including one that failed or was
interrupted - leaves one file a person can read in ``logs/``; see :mod:`plt.pipeline.runlog`,
which exists because this command is about to run weekly with nobody watching it.

**Self-describing from the store, not from the process** (``docs/architecture.md`` rule 2.11).
What the manifest says the corpus *contains* is counted off the disk by :meth:`CorpusStore.survey`
every time it is written: the cases, the resource types with a count each, the languages held,
the span of decision dates and of fetch instants. It is not taken from ``Settings``, because a
setting states what a process was asked to do and a corpus is what a process actually left
behind. The two drifted apart once already — two runs launched without
``PLT_EURLEX_RESOURCE_TYPES`` set rewrote the manifest of a 100,000-case store captured under
seventeen resource types with the four-type default, and the next repair to trust it would have
compared the store against a corpus a third its size and reported it complete. The run's own
configuration is still recorded, under ``configuration``, labelled as a fact about the run.

One rule is inherited from the ingestion runner deliberately: **the checkpoint stops
advancing at the first case that failed**, so nothing after a failure is ever considered
done. A case that fails every run holds its window open and keeps appearing in the failure
count, which is how an operator finds out about it. Re-enumerating that window costs
discovery queries but no fetches, because everything already on disk is skipped.

That last sentence is the one that turned out to be too comfortable, and
:mod:`plt.pipeline.repair` is the answer to it. "Discovery queries but no fetches" is not
free: for CELLAR it is a counting query per window and a grouped, join-carrying page query
per window that holds anything, and re-running the whole walk to find a few thousand absences
replays every one of them over the hundred thousand cases already on disk. The repair reuses
everything in this module — the store, :func:`mirror_case`, the checkpoint value object, the
manifest, the run log — and replaces only where the list of cases comes from.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import SecretStr

from plt import __version__
from plt.config import Settings, get_settings
from plt.db.models import IngestStatus
from plt.pipeline.base import (
    Candidate,
    ConnectorError,
    NormalisedCase,
    NormalisedDocument,
    PipelineError,
    RawDocument,
    SourceConnector,
    SourceTraffic,
    SourceUnavailableError,
)
from plt.pipeline.checkpoint import Checkpoint, resolve_since
from plt.pipeline.registry import connector_for
from plt.pipeline.runlog import LOG_DIR_NAME, RunMode, write_run_log
from plt.utils.logging import get_logger
from plt.utils.shutdown import StopRequest

__all__ = [
    "CorpusStore",
    "CorpusStoreError",
    "CorpusSurvey",
    "MirrorCounters",
    "MirrorFailure",
    "MirrorReport",
    "RunWork",
    "StoredFile",
    "case_folder_name",
    "final_status",
    "mirror_case",
    "mirror_jurisdiction",
    "rebuild_manifest",
    "run_against_store",
]

log = get_logger(__name__)

#: Marks a case folder complete. Written last, so a run killed mid-case leaves a folder that
#: the next run overwrites rather than one it mistakes for finished.
_METADATA_NAME: Final[str] = "metadata.json"

#: The connector's own source response, under the name the Dutch store already uses.
_SOURCE_RECORD_STEM: Final[str] = "raw_content"

#: One language version of the full text.
_FULL_TEXT_STEM: Final[str] = "fulltext"

#: Corpus-level bookkeeping, beside the case folders. No case may take one of these names —
#: ``logs`` included, or a source identifier that sanitises onto it would scatter payload
#: files through the run logs.
_MANIFEST_NAME: Final[str] = "manifest.json"
_CHECKPOINT_NAME: Final[str] = "_checkpoint.json"
_REPAIR_CHECKPOINT_NAME: Final[str] = "_repair_checkpoint.json"
_FAILURES_NAME: Final[str] = "_failures.jsonl"
_RESERVED_FILES: Final[frozenset[str]] = frozenset(
    {
        _MANIFEST_NAME.lower(),
        _CHECKPOINT_NAME.lower(),
        _REPAIR_CHECKPOINT_NAME.lower(),
        _FAILURES_NAME.lower(),
        LOG_DIR_NAME.lower(),
    }
)

#: Version of the on-disk layout, recorded in the manifest so a reader of a store taken
#: today can tell whether it is the layout the code in front of them expects. Version 2
#: describes the corpus from the corpus: ``contents`` is counted off the disk, and what
#: version 1 called ``source`` is ``configuration``, which is the run's and says so.
_LAYOUT_VERSION: Final[int] = 2

#: Runs kept in the manifest's history. A capture interrupted nightly for a fortnight should
#: not grow the manifest without bound; the cumulative totals beside it are complete.
_MANIFEST_RUN_HISTORY: Final[int] = 50

#: Failures a run keeps in memory for its own log. Every failure is counted and written to
#: ``_failures.jsonl`` whatever this is; only the sample the log prints is bounded, so a run
#: where the source refused everything still holds a fixed amount (architecture rule 2.3).
_FAILURE_SAMPLE: Final[int] = 20

#: Characters allowed in a case folder name. Everything else is replaced, which is what turns
#: ``ECLI:NL:CBB:1994:ZG1226`` into the folder the Dutch store already holds. No slash, no
#: backslash and no colon can survive, so a source identifier cannot express a path.
_UNSAFE_IN_NAME: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9()._-]")

#: Longest case folder name. Well inside every filesystem's limit, and short enough that the
#: full path stays under Windows' default 260-character ceiling for a reasonably placed store.
_MAX_FOLDER_NAME: Final[int] = 100

#: Names Windows refuses to create a directory under, whatever the extension.
_RESERVED_DEVICE_NAMES: Final[frozenset[str]] = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{digit}" for digit in "123456789"}
    | {f"lpt{digit}" for digit in "123456789"}
)

#: Non-alphanumerics are stripped from a format before it becomes a file extension.
_NON_EXTENSION: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]")

#: Fallback extension for a payload whose format the connector did not name.
_DEFAULT_EXTENSION: Final[str] = "bin"

#: Language tag used for a payload whose language the connector could not determine.
_UNKNOWN_LANGUAGE: Final[str] = "und"

#: Where a connector records what kind of record a case is, inside the case's
#: ``source_metadata``. The mirror knows no jurisdiction's vocabulary and does not need to: it
#: counts the values it finds, whatever they are, so ``JUDG`` and ``OPIN_AG`` reach the
#: manifest without this module having heard of either. A connector with no such notion — the
#: Dutch one — simply leaves the breakdown empty.
_RESOURCE_TYPE_KEY: Final[str] = "resource_type"

#: Distinct values one observed breakdown may hold before the rest are counted together. A
#: survey walks a corpus of any size, so what it accumulates has to be bounded by something
#: other than the corpus (architecture rule 2.3). A source has tens of resource types and tens
#: of languages, so a store that is what it claims to be never reaches this; one that is not
#: says so in a bucket rather than in the process's memory.
_MAX_OBSERVED_VALUES: Final[int] = 256

#: Where values past that bound are counted.
_OBSERVED_OVERFLOW: Final[str] = "(other)"

#: Outcomes that entitle a run to say how the corpus was taken. A run that failed or was
#: interrupted did not finish the work it was configured for, so it leaves the recorded
#: configuration alone rather than replacing it with its own.
_CAPTURED_SOMETHING: Final[frozenset[IngestStatus]] = frozenset(
    {IngestStatus.SUCCESS, IngestStatus.PARTIAL}
)

#: Printed in the manifest beside the settings, because the mistake this prevents was a
#: reading mistake: a person looked at a block of settings and read it as the corpus's scope.
_CONFIGURATION_NOTE: Final[str] = (
    "The settings the run named in recorded_by was launched with. This describes that run, "
    "not this corpus: what the corpus actually contains is in contents, counted from the "
    "store itself. Cite contents."
)


class CorpusStoreError(PipelineError):
    """The store could not hold a case: an unusable identifier, or a name collision."""


def case_folder_name(source_id: str) -> str:
    """Return the folder one case is stored under.

    Every character outside ``A-Za-z0-9().-_`` is replaced with an underscore, which is what
    maps ``ECLI:NL:CBB:1994:ZG1226`` onto the folder name the Dutch store already uses and
    leaves a CELEX number — including a parenthesised corrigendum suffix such as
    ``62021TO0601(01)`` — untouched. Because the mapping only ever *replaces* characters, two
    identifiers can in principle meet on one name; the true identifier is therefore written
    into ``metadata.json`` and checked before a case is stored, so a collision is refused
    loudly rather than silently overwriting a case.

    Args:
        source_id: The source's own identifier for the case.

    Returns:
        A single path segment, safe on Windows and POSIX alike.

    Raises:
        CorpusStoreError: If the identifier is blank, too long, hidden, ends in a dot, names
            a Windows device, or collides with the corpus's own bookkeeping files. All of
            these are refused rather than repaired: a case stored under a name nobody can
            predict is a case nobody can find again.
    """
    name = _UNSAFE_IN_NAME.sub("_", source_id.strip())
    if not name:
        message = f"{source_id!r} yields no usable folder name"
        raise CorpusStoreError(message)
    if len(name) > _MAX_FOLDER_NAME:
        message = f"{source_id!r} is longer than the {_MAX_FOLDER_NAME}-character folder limit"
        raise CorpusStoreError(message)
    if name.startswith("."):
        message = f"{source_id!r} would be stored as a hidden folder"
        raise CorpusStoreError(message)
    if name.endswith("."):
        message = f"{source_id!r} ends in a dot, which Windows drops from a folder name"
        raise CorpusStoreError(message)
    if name.split(".", 1)[0].lower() in _RESERVED_DEVICE_NAMES:
        message = f"{source_id!r} names a reserved device"
        raise CorpusStoreError(message)
    if name.lower() in _RESERVED_FILES:
        message = f"{source_id!r} collides with the corpus's own bookkeeping files"
        raise CorpusStoreError(message)
    return name


def _extension(media_format: str | None) -> str:
    """Turn a connector's format name into a file extension.

    Args:
        media_format: Format the connector named, e.g. ``xml``, ``xhtml``, ``text``.

    Returns:
        A short alphanumeric extension. ``text`` becomes ``txt``; an unnamed or unusable
        format becomes ``bin``, so a payload is never dropped for want of a name.
    """
    cleaned = _NON_EXTENSION.sub("", (media_format or "").lower())[:8]
    if not cleaned:
        return _DEFAULT_EXTENSION
    return "txt" if cleaned == "text" else cleaned


def _language_tag(language: str | None) -> str:
    """Return the tag a full-text file is named by.

    Args:
        language: ISO 639-1 code of the version, or ``None``.

    Returns:
        The lower-cased code with anything unusable stripped, or ``und`` when the connector
        could not say — the ISO 639-2 code for an undetermined language.
    """
    cleaned = _NON_EXTENSION.sub("", (language or "").lower())[:8]
    return cleaned or _UNKNOWN_LANGUAGE


@dataclass(frozen=True, slots=True)
class StoredFile:
    """One payload file inside a case folder.

    Attributes:
        name: File name within the folder.
        role: ``source_record`` for the connector's own response, ``full_text`` for a
            language version of the decision.
        language: ISO 639-1 code of the version, where it has one.
        media_format: Format the connector named the payload.
        content_type: Content type the source stated, where it stated one.
        source_url: URL the payload was retrieved from.
        size_bytes: Size on disk, as written.
    """

    name: str
    role: str
    language: str | None = None
    media_format: str | None = None
    content_type: str | None = None
    source_url: str | None = None
    size_bytes: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form written into ``metadata.json``."""
        return {
            "name": self.name,
            "role": self.role,
            "language": self.language,
            "format": self.media_format,
            "content_type": self.content_type,
            "source_url": self.source_url,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class _Payload:
    """A payload on its way to disk, before it has been written.

    Attributes:
        text: The payload exactly as the connector received it.
        stored: How it will be described in ``metadata.json``, bar its size.
    """

    text: str
    stored: StoredFile


def _payloads(raw: RawDocument, case: NormalisedCase | None) -> list[_Payload]:
    """Decide which files a case folder holds, and what each is called.

    The connector's own response is always written, under the ``raw_content`` name the Dutch
    store already uses. A normalised case may carry further verbatim payloads — the EU
    connector returns one per language version of the full text — and each of those becomes
    a ``fulltext.<language>.<format>`` file. A document whose payload *is* the source
    response is not written twice.

    Args:
        raw: The connector's response.
        case: The normalised case, or ``None`` when normalisation failed and only the source
            response can be described.

    Returns:
        The files to write, source record first.
    """
    source_format = raw.media_format
    files = [
        _Payload(
            text=raw.payload,
            stored=StoredFile(
                name=f"{_SOURCE_RECORD_STEM}.{_extension(source_format)}",
                role="source_record",
                language=_source_record_language(raw, case),
                media_format=source_format,
                content_type=_content_type(raw.source_metadata),
                source_url=raw.source_url or raw.candidate.source_url,
            ),
        )
    ]
    if case is None:
        return files
    taken = {files[0].stored.name}
    for index, document in enumerate(case.documents):
        payload = document.raw_payload
        if not payload or payload == raw.payload:
            continue
        name = _full_text_name(document, index, taken)
        taken.add(name)
        files.append(
            _Payload(
                text=payload,
                stored=StoredFile(
                    name=name,
                    role="full_text",
                    language=document.language,
                    media_format=document.media_format,
                    content_type=_content_type(document.source_metadata),
                    source_url=document.source_url,
                ),
            )
        )
    return files


def _source_record_language(raw: RawDocument, case: NormalisedCase | None) -> str | None:
    """Return the language the source record is itself the text of, if it is one.

    A source that serves the decision rather than a notice about it makes one file do both
    jobs: the payload is not written twice, so the language version it carries would go
    unrecorded unless the source record names it. What that costs is not tidiness — it is the
    store's own statement of which languages it holds text in, which is counted from these
    names (rule 2.11).

    Args:
        raw: The connector's response.
        case: The normalised case, or ``None``.

    Returns:
        The language of the document whose payload is the source response, or ``None`` when
        the response is a notice rather than a text.
    """
    if case is None:
        return None
    for document in case.documents:
        if document.raw_payload and document.raw_payload == raw.payload:
            return document.language
    return None


def _full_text_name(document: NormalisedDocument, index: int, taken: set[str]) -> str:
    """Name one language version of the full text.

    Args:
        document: The document to name.
        index: Its position among the case's documents, used to break a tie.
        taken: Names already used in this folder.

    Returns:
        ``fulltext.<language>.<format>``, suffixed with the document's position when a case
        publishes two versions in the same language — which is not expected, but must not
        cost one of them its file.
    """
    tag = _language_tag(document.language)
    extension = _extension(document.media_format)
    name = f"{_FULL_TEXT_STEM}.{tag}.{extension}"
    if name in taken:
        name = f"{_FULL_TEXT_STEM}.{tag}-{index}.{extension}"
    return name


def _content_type(metadata: Mapping[str, Any]) -> str | None:
    """Read the content type a connector recorded beside a payload.

    Args:
        metadata: The connector's ``source_metadata`` for the payload.

    Returns:
        The content type, or ``None`` when the connector recorded none. Two spellings are
        accepted because a connector may record the response's or the notice's.
    """
    for key in ("content_type", "notice_content_type"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _jsonable(value: object) -> object:
    """Render a value ``json`` cannot serialise on its own.

    Args:
        value: The value.

    Returns:
        An ISO 8601 string for a date or instant, the string form of anything else. Used as
        ``json.dump``'s ``default``, so a connector that puts an unexpected type in its
        ``source_metadata`` costs a slightly lossy field rather than the whole case.
    """
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _dump(record: Mapping[str, Any]) -> bytes:
    """Serialise a bookkeeping record the way every file in the store is written.

    Args:
        record: The record.

    Returns:
        Indented UTF-8 JSON with non-ASCII characters left as themselves, so a case title in
        Greek or Bulgarian is readable in the file rather than escaped.
    """
    return json.dumps(record, indent=2, ensure_ascii=False, default=_jsonable).encode("utf-8")


@dataclass(frozen=True, slots=True)
class CorpusSurvey:
    """What a store contains, counted from the store.

    Every field here is an observation. Nothing in it is taken from ``Settings``, from a run's
    counters or from the previous manifest, which is the point: a corpus's own description has
    to be a fact about the corpus (``docs/architecture.md`` rule 2.11). The walk is local and
    costs no request — one directory listing and one small read per case — so the honest answer
    is also the cheap one.

    Attributes:
        observed_at: When the walk was made. A survey ages the moment a run writes another
            case, so it says when it was true.
        cases: Case folders holding a ``metadata.json``, which is what marks a case complete.
        unreadable: How many of those held metadata that could not be parsed. Counted as cases
            — the payloads are there — but they contribute to no breakdown below, so a reader
            can see how much of the description rests on files that could not be read.
        payload_bytes: Sum of the payload sizes each case's own record states.
        resource_types: How many cases of each kind of record the store holds, as the cases
            themselves say. Empty for a source with no such notion.
        languages: How many cases the store holds text for, per language. Counted per case
            rather than per file, so a case whose source record and full text are the same
            language is one case in that language and not two.
        earliest_decision: Earliest decision date on any case, ISO 8601.
        latest_decision: Latest decision date on any case.
        first_fetched_at: When the earliest-fetched case in the store was fetched. This and
            the next are the capture window as it actually happened, beside the one the runs
            asked for.
        last_fetched_at: When the latest-fetched case was fetched.
    """

    observed_at: datetime
    cases: int = 0
    unreadable: int = 0
    payload_bytes: int = 0
    resource_types: Mapping[str, int] = field(default_factory=dict)
    languages: Mapping[str, int] = field(default_factory=dict)
    earliest_decision: str | None = None
    latest_decision: str | None = None
    first_fetched_at: str | None = None
    last_fetched_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form written into the manifest's ``contents`` block."""
        return {
            "observed_at": self.observed_at.isoformat(),
            "cases": self.cases,
            "resource_types": dict(self.resource_types),
            "languages": dict(self.languages),
            "decision_dates": {"earliest": self.earliest_decision, "latest": self.latest_decision},
            "fetched_at": {"earliest": self.first_fetched_at, "latest": self.last_fetched_at},
            "payload_bytes": self.payload_bytes,
            "unreadable": self.unreadable,
        }


class _Span:
    """The earliest and latest of a stream of stored dates, kept as they were written.

    The comparison is on the parsed instant, so an offset cannot make one string sort before
    another it is later than; what is reported is the original text, so nothing a case recorded
    is reworded on its way into the manifest.
    """

    __slots__ = ("_earliest", "_latest")

    def __init__(self) -> None:
        """Start with nothing seen."""
        self._earliest: tuple[datetime, str] | None = None
        self._latest: tuple[datetime, str] | None = None

    def add(self, value: object) -> None:
        """Offer one stored date or instant.

        Args:
            value: The stored value. Anything that is not a readable ISO 8601 date or instant
                is ignored: a corpus with one unparseable date should still be able to state
                the span of the rest.
        """
        if not isinstance(value, str):
            return
        moment = _moment(value)
        if moment is None:
            return
        if self._earliest is None or moment < self._earliest[0]:
            self._earliest = (moment, value)
        if self._latest is None or moment > self._latest[0]:
            self._latest = (moment, value)

    @property
    def earliest(self) -> str | None:
        """Return the earliest value seen, as it was stored."""
        return self._earliest[1] if self._earliest is not None else None

    @property
    def latest(self) -> str | None:
        """Return the latest value seen, as it was stored."""
        return self._latest[1] if self._latest is not None else None


class _Tally:
    """The running total of a survey, bounded whatever the corpus turns out to hold."""

    __slots__ = (
        "cases",
        "decisions",
        "fetches",
        "languages",
        "payload_bytes",
        "resource_types",
        "unreadable",
    )

    def __init__(self) -> None:
        """Start an empty tally."""
        self.cases = 0
        self.unreadable = 0
        self.payload_bytes = 0
        self.resource_types: Counter[str] = Counter()
        self.languages: Counter[str] = Counter()
        self.decisions = _Span()
        self.fetches = _Span()

    def add(self, record: Mapping[str, Any] | None) -> None:
        """Count one case folder.

        Args:
            record: The case's ``metadata.json``, or ``None`` when it could not be read. An
                unreadable record still counts as a case — the payloads beside it are on disk
                — and contributes to no breakdown.
        """
        self.cases += 1
        if record is None:
            self.unreadable += 1
            return
        source_metadata = record.get("source_metadata")
        if isinstance(source_metadata, Mapping):
            _count_value(self.resource_types, source_metadata.get(_RESOURCE_TYPE_KEY))
        languages: set[str] = set()
        files = record.get("files")
        if isinstance(files, list):
            for item in files:
                self._add_file(item, languages)
        for language in sorted(languages):
            _count_value(self.languages, language)
        self.decisions.add(record.get("decision_date"))
        self.fetches.add(record.get("fetched_at"))

    def _add_file(self, item: object, languages: set[str]) -> None:
        """Count one payload file listed in a case's record.

        Args:
            item: The file's entry.
            languages: The languages this case holds text in, added to. Every stored file that
                names a language counts, the source record included: where a source serves the
                decision itself rather than a notice about it, that file *is* the text, and a
                measure that ignored it would report a corpus with no language coverage at all.
        """
        if not isinstance(item, Mapping):
            return
        size = item.get("size_bytes")
        if isinstance(size, int) and not isinstance(size, bool) and size > 0:
            self.payload_bytes += size
        language = item.get("language")
        if isinstance(language, str) and language.strip():
            languages.add(language.strip())

    def finish(self, observed_at: datetime) -> CorpusSurvey:
        """Return the survey these counts add up to.

        Args:
            observed_at: When the walk began.

        Returns:
            The survey, its breakdowns ordered by count and then by name so that two surveys
            of the same store produce the same file.
        """
        return CorpusSurvey(
            observed_at=observed_at,
            cases=self.cases,
            unreadable=self.unreadable,
            payload_bytes=self.payload_bytes,
            resource_types=_ordered(self.resource_types),
            languages=_ordered(self.languages),
            earliest_decision=self.decisions.earliest,
            latest_decision=self.decisions.latest,
            first_fetched_at=self.fetches.earliest,
            last_fetched_at=self.fetches.latest,
        )


def _count_value(counts: Counter[str], value: object) -> None:
    """Count one observed value, without letting the tally grow with the corpus.

    Args:
        counts: The breakdown to count into.
        value: The value a case recorded. A blank or non-string value is not counted: a
            breakdown is a statement about what is there, and "absent" is not a kind.
    """
    if not isinstance(value, str) or not value.strip():
        return
    name = value.strip()
    if name not in counts and len(counts) >= _MAX_OBSERVED_VALUES:
        counts[_OBSERVED_OVERFLOW] += 1
        return
    counts[name] += 1


def _ordered(counts: Counter[str]) -> dict[str, int]:
    """Return a breakdown in the order a person would want to read it.

    Args:
        counts: The counted values.

    Returns:
        The same counts, largest first and ties broken by name.
    """
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return dict(ranked)


def _moment(value: str) -> datetime | None:
    """Parse a stored ISO 8601 date or instant into something comparable.

    Args:
        value: The stored text.

    Returns:
        An aware instant — a bare date is read as midnight UTC and a naive instant as UTC — or
        ``None`` when the text is neither.
    """
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class CorpusStore:
    """The on-disk store of one jurisdiction's corpus.

    Owns the layout and nothing else: what a case folder is called, which files are in it,
    where the checkpoint and the manifest live. It knows nothing about how a case was
    obtained, which is what lets the same store serve any connector.
    """

    def __init__(self, root: Path, jurisdiction_code: str) -> None:
        """Bind the store to a directory.

        Args:
            root: Root of the shared case-law store, holding one directory per jurisdiction.
            jurisdiction_code: Jurisdiction whose directory this is, e.g. ``EU``.

        Raises:
            CorpusStoreError: If the jurisdiction code is not two ASCII letters. It becomes a
                path segment, so it is checked here rather than trusted.
        """
        code = jurisdiction_code.strip().upper()
        if len(code) != 2 or not code.isascii() or not code.isalpha():
            message = f"jurisdiction code must be two ASCII letters, got {jurisdiction_code!r}"
            raise CorpusStoreError(message)
        self._root = Path(root).expanduser()
        self._code = code
        self._path = self._root / code

    @property
    def path(self) -> Path:
        """Return the directory this jurisdiction's cases are stored in."""
        return self._path

    @property
    def jurisdiction_code(self) -> str:
        """Return the jurisdiction code this store holds."""
        return self._code

    def prepare(self) -> None:
        """Create the jurisdiction's directory if it is not there yet.

        Raises:
            CorpusStoreError: If the directory cannot be created, which is worth failing the
                run over: nothing else it does would be recorded.
        """
        try:
            self._path.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            message = f"the corpus store at {self._path} could not be opened: {error}"
            raise CorpusStoreError(message) from error

    def case_dir(self, source_id: str) -> Path:
        """Return the folder one case is stored in.

        Args:
            source_id: The source's own identifier.

        Returns:
            The folder path, which may not exist yet.

        Raises:
            CorpusStoreError: If the identifier cannot be turned into a folder name.
        """
        return self._path / case_folder_name(source_id)

    def holds(self, source_id: str) -> bool:
        """Return whether the store already holds this case, complete.

        ``metadata.json`` is written last and carries the identifier, so its presence means
        the payloads beside it are complete and its content proves the folder is this case's
        rather than a sanitised near-miss.

        Args:
            source_id: The source's own identifier.

        Returns:
            Whether the case is on disk and finished.

        Raises:
            CorpusStoreError: If the identifier cannot be turned into a folder name.
        """
        stored = self._read_case_metadata(self.case_dir(source_id))
        return stored is not None and stored.get("identifier") == source_id

    def write_case(
        self,
        source_id: str,
        payloads: Sequence[_Payload],
        metadata: Mapping[str, Any],
    ) -> tuple[int, tuple[StoredFile, ...]]:
        """Write one case: its payloads, then the metadata that marks it complete.

        Args:
            source_id: The source's own identifier.
            payloads: The verbatim payloads to store, source record first.
            metadata: The index and provenance fields to record beside them. The file list is
                added here, since only this method knows what was actually written.

        Returns:
            The number of bytes written and the files as they were stored.

        Raises:
            CorpusStoreError: If the folder is already occupied by a *different* case, or the
                payloads cannot be written.
        """
        directory = self.case_dir(source_id)
        occupant = self._read_case_metadata(directory)
        if occupant is not None and occupant.get("identifier") not in (None, source_id):
            message = (
                f"{source_id!r} and {occupant.get('identifier')!r} both map onto the folder "
                f"{directory.name!r}; refusing to overwrite one with the other"
            )
            raise CorpusStoreError(message)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            written = 0
            stored: list[StoredFile] = []
            for payload in payloads:
                data = payload.text.encode("utf-8")
                (directory / payload.stored.name).write_bytes(data)
                written += len(data)
                stored.append(
                    StoredFile(
                        name=payload.stored.name,
                        role=payload.stored.role,
                        language=payload.stored.language,
                        media_format=payload.stored.media_format,
                        content_type=payload.stored.content_type,
                        source_url=payload.stored.source_url,
                        size_bytes=len(data),
                    )
                )
            record = {**metadata, "files": [item.as_dict() for item in stored]}
            body = _dump(record)
            _replace(directory / _METADATA_NAME, body)
        except OSError as error:
            message = f"{source_id}: the case could not be written to {directory}: {error}"
            raise CorpusStoreError(message) from error
        return written + len(body), tuple(stored)

    def _read_case_metadata(self, directory: Path) -> Mapping[str, Any] | None:
        """Read a case folder's metadata, if it has any.

        Args:
            directory: The case folder.

        Returns:
            The parsed record, or ``None`` when the folder is absent, unfinished or holds
            metadata that cannot be read — all of which mean "not stored", so the case is
            fetched again and the file overwritten.
        """
        try:
            body = (directory / _METADATA_NAME).read_bytes()
        except OSError:
            return None
        try:
            record = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.warning(
                "unreadable case metadata; treating the case as absent",
                extra={"context": {"path": str(directory)}},
            )
            return None
        return record if isinstance(record, dict) else None

    # -- corpus-level bookkeeping ------------------------------------------------------

    def read_checkpoint(self, connector: str) -> Checkpoint | None:
        """Read where the last capture of this connector stopped.

        Args:
            connector: Connector name, which the stored position must match — a store
                repopulated by a different connector must not resume from the old one's
                cursor.

        Returns:
            The stored position, or ``None`` when there is none to resume from.
        """
        return self._read_checkpoint(_CHECKPOINT_NAME, connector)

    def write_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Store where the next capture should resume.

        Args:
            checkpoint: The position to store. It describes cases already on disk, so it is
                written whatever the outcome of the run — unlike the ingestion checkpoint,
                whose work can still be rolled back when a run fails.

        Raises:
            CorpusStoreError: If it cannot be written; a run that cannot record its position
                would silently start over.
        """
        self._write_checkpoint(_CHECKPOINT_NAME, checkpoint)

    def read_repair_checkpoint(self, connector: str) -> Checkpoint | None:
        """Read how far the last repair read the source's identifier listing.

        A file of its own, deliberately. The capture's position is a high-water mark on the
        source's modification dates and is what supplies the next capture's window; a repair's
        is a place in a listing and means nothing as a window. Writing one over the other
        would make the next capture resume from a bound no run had actually walked to.

        Args:
            connector: Connector name, which the stored position must match.

        Returns:
            The stored position, or ``None`` when there is none.
        """
        return self._read_checkpoint(_REPAIR_CHECKPOINT_NAME, connector)

    def write_repair_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Store how far a repair has read the identifier listing.

        Args:
            checkpoint: The position to store.

        Raises:
            CorpusStoreError: If it cannot be written.
        """
        self._write_checkpoint(_REPAIR_CHECKPOINT_NAME, checkpoint)

    def _read_checkpoint(self, name: str, connector: str) -> Checkpoint | None:
        """Read one of the store's stored positions.

        Args:
            name: File the position is kept in.
            connector: Connector name the stored position must match.

        Returns:
            The stored position, or ``None`` when the file is absent, unreadable or belongs
            to another connector — all of which mean "nothing to resume from".
        """
        try:
            body = (self._path / name).read_bytes()
        except OSError:
            return None
        try:
            record = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.warning(
                "unreadable stored position; starting from the beginning",
                extra={"context": {"path": str(self._path / name)}},
            )
            return None
        if not isinstance(record, dict) or record.get("connector") != connector:
            return None
        return Checkpoint(
            connector=connector,
            jurisdiction_code=str(record.get("jurisdiction_code") or self._code),
            last_modified_seen=_instant(record.get("last_modified_seen")),
            last_cursor=_text(record.get("last_cursor")),
            last_source_id=_text(record.get("last_source_id")),
            updated_at=_instant(record.get("updated_at")),
        )

    def _write_checkpoint(self, name: str, checkpoint: Checkpoint) -> None:
        """Store one of the store's positions.

        Written whole and replaced atomically, so a process killed here leaves either the old
        position or the new one and never half of either.

        Args:
            name: File the position belongs in.
            checkpoint: The position to store.

        Raises:
            CorpusStoreError: If it cannot be written.
        """
        record = {**checkpoint.as_dict(), "updated_at": datetime.now(UTC).isoformat()}
        try:
            _replace(self._path / name, _dump(record))
        except OSError as error:
            message = f"{name} could not be written to {self._path}: {error}"
            raise CorpusStoreError(message) from error

    def record_failure(self, source_id: str, reason: str) -> None:
        """Append one case that did not come down, and why.

        Args:
            source_id: The case's identifier.
            reason: What went wrong, as the connector reported it.
        """
        entry = {
            "identifier": source_id,
            "at": datetime.now(UTC).isoformat(),
            "reason": reason,
        }
        line = json.dumps(entry, ensure_ascii=False, default=_jsonable) + "\n"
        try:
            with (self._path / _FAILURES_NAME).open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError as error:  # pragma: no cover - the store was writable a moment ago
            log.warning(
                "a failure could not be recorded",
                extra={"context": {"source_id": source_id, "error": str(error)}},
            )

    def count_cases(self) -> int:
        """Count the complete case folders on disk.

        Returns:
            How many folders hold a ``metadata.json``. Read from the filesystem rather than
            from a counter, because the manifest's total is what a reader of the corpus will
            cite and a counter can only ever be what the code believed. This is the stat-only
            answer, for a log line mid-run; :meth:`survey` is the one the manifest is written
            from, and the two agree on what a case is.
        """
        try:
            entries = list(self._path.iterdir())
        except OSError:
            return 0
        return sum(1 for entry in entries if (entry / _METADATA_NAME).is_file())

    def iter_cases(self) -> Iterator[tuple[Path, Mapping[str, Any]]]:
        """Yield every complete case folder on disk, with the metadata beside it.

        The order is the filesystem's, not the source's: this walks a corpus, and a corpus
        has no window. A caller that needs the cases in modification order has to sort them,
        which is why nothing here pretends to.

        Only folders holding a readable ``metadata.json`` are yielded, on the same rule
        :meth:`holds` uses — the file is written last, so its presence is what makes a folder
        a case rather than a half-written one.

        Yields:
            The folder and its metadata record, one pair per case.
        """
        try:
            entries = self._path.iterdir()
        except OSError as error:
            log.warning(
                "the store could not be listed; nothing will be read from it",
                extra={"context": {"path": str(self._path), "error": str(error)}},
            )
            return
        for entry in entries:
            record = self._read_case_metadata(entry)
            if record is not None:
                yield entry, record

    def survey(self) -> CorpusSurvey:
        """Describe the corpus by reading it: what kinds of case, in what languages, how many.

        This is what the manifest's ``contents`` block is written from, and it is a walk of the
        store rather than a summary of the run that happened to write it last. A run states
        what it was configured to fetch; only the disk states what is here, and the two are not
        the same claim (``docs/architecture.md`` rule 2.11).

        The walk reads one small file per case and holds a fixed number of counters, so it is
        bounded by the settings rather than by the corpus (rule 2.3), and it sends nothing:
        roughly a hundred thousand cases in a few seconds, against a run that took hours.

        Returns:
            The survey. A store that cannot be walked, or that is not there at all, yields an
            empty one rather than an error: a corpus is not lost because it could not be
            described, and the count of zero is itself the observation.
        """
        observed_at = datetime.now(UTC)
        tally = _Tally()
        try:
            for entry in self._path.iterdir():
                if (entry / _METADATA_NAME).is_file():
                    tally.add(self._read_case_metadata(entry))
        except OSError as error:
            log.warning(
                "the store could not be walked to the end; the survey describes what was read",
                extra={"context": {"path": str(self._path), "error": str(error)}},
            )
        return tally.finish(observed_at)

    def read_manifest(self) -> dict[str, Any]:
        """Read the corpus manifest, or an empty record when there is none yet.

        Returns:
            The parsed manifest, or ``{}``.
        """
        try:
            body = (self._path / _MANIFEST_NAME).read_bytes()
        except OSError:
            return {}
        try:
            record = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return record if isinstance(record, dict) else {}

    def write_manifest(self, record: Mapping[str, Any]) -> None:
        """Store the corpus manifest.

        Args:
            record: The manifest to write.

        Raises:
            CorpusStoreError: If it cannot be written.
        """
        try:
            _replace(self._path / _MANIFEST_NAME, _dump(record))
        except OSError as error:
            message = f"the corpus manifest could not be written to {self._path}: {error}"
            raise CorpusStoreError(message) from error

    # -- run logs ----------------------------------------------------------------------

    @property
    def log_dir(self) -> Path:
        """Return the directory this jurisdiction's run logs are written to."""
        return self._path / LOG_DIR_NAME

    def write_run_log(self, name: str, text: str) -> Path:
        """Write one run's log, without ever replacing another run's.

        Args:
            name: File name the run chose, from :func:`plt.pipeline.runlog.run_log_name`.
            text: The rendered log.

        Returns:
            The file as written. A name already taken - two runs started in the same second -
            gains an underscore and a number rather than overwriting the earlier record,
            which is the one thing a log directory must not do. The suffix sorts *after* the
            unsuffixed name, so a listing stays in the order the runs happened.

        Raises:
            OSError: If the directory or the file cannot be written. The caller reports it
                and carries on: a run that has already stored its cases is not lost because
                its log could not be.
        """
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / name
        stem, suffix = path.stem, path.suffix
        attempt = 2
        while path.exists():
            path = self.log_dir / f"{stem}_{attempt}{suffix}"
            attempt += 1
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def run_logs(self) -> list[Path]:
        """Return the run logs on disk.

        Returns:
            Every file in the log directory, unsorted and unfiltered - the caller decides
            which of them it wrote and may therefore delete. Empty when there is no log
            directory yet.
        """
        try:
            return [entry for entry in self.log_dir.iterdir() if entry.is_file()]
        except OSError:
            return []


def _replace(path: Path, body: bytes) -> None:
    """Write a file whole, replacing any previous version in one step.

    Args:
        path: Where the file belongs.
        body: Its contents.
    """
    temporary = path.with_name(f"{path.name}.partial")
    temporary.write_bytes(body)
    temporary.replace(path)


def _instant(value: object) -> datetime | None:
    """Parse a stored ISO 8601 instant.

    Args:
        value: The stored value.

    Returns:
        The instant in UTC, or ``None`` when it is absent or unreadable.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _mapping(value: object) -> Mapping[str, Any]:
    """Read a nested object out of a stored record.

    Args:
        value: The stored value, which a hand-edited file could have made anything.

    Returns:
        The mapping with string keys, or an empty one. A manifest that has been edited into
        something unreadable costs its history rather than the run that has to rewrite it.
    """
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _text(value: object) -> str | None:
    """Return a stored string, or ``None`` when it is absent or blank.

    Args:
        value: The stored value.

    Returns:
        The string, stripped, or ``None``.
    """
    return value.strip() or None if isinstance(value, str) else None


@dataclass(slots=True)
class MirrorCounters:
    """What a mirror run did, counted.

    Every candidate discovered ends in exactly one of ``mirrored``, ``skipped`` or
    ``errors``.

    Attributes:
        discovered: Candidates the connector's discovery yielded.
        mirrored: Cases fetched and written.
        skipped: Cases already complete on disk, which cost no request at all.
        errors: Cases that could not be stored. Named as the ingestion runner names it, so
            one exit-code policy serves both commands.
        bytes_written: Bytes written to disk by this run, payloads and metadata together.
    """

    discovered: int = 0
    mirrored: int = 0
    skipped: int = 0
    errors: int = 0
    bytes_written: int = 0


@dataclass(frozen=True, slots=True)
class MirrorFailure:
    """One case that did not come down, kept for the run's own log.

    ``_failures.jsonl`` holds the store's whole history; this is the handful the run log
    prints, so a reader sees what went wrong without opening a file that spans years.

    Attributes:
        source_id: The case's identifier.
        reason: What went wrong, as the connector reported it.
    """

    source_id: str
    reason: str


@dataclass(slots=True)
class MirrorReport:
    """The outcome of one mirror run, for the CLI, the log and the manifest.

    Attributes:
        jurisdiction_code: Jurisdiction mirrored.
        connector: Connector that supplied the cases.
        store_path: Directory the cases were written to.
        mode: How the run got its list of cases — a discovery walk or a targeted repair. The
            counters mean different things in the two, so the log and the manifest both have
            to be able to tell which they are reading.
        started_at: When the run began.
        finished_at: When it ended, or ``None`` while it is running.
        status: Terminal state, in the ingestion pipeline's own vocabulary.
        counters: What it did.
        window_since: Lower bound actually used, after the checkpoint was consulted.
        window_until: Upper bound the caller pinned, if any.
        checkpoint_before: Position the run started from.
        checkpoint_after: Position it left behind.
        error_message: Why it failed, when it did.
        limit: Cap the caller put on how many cases would be fetched, if any. Recorded
            because a rehearsal that stopped at fifty cases and a run that found fifty are
            indistinguishable in the counts alone.
        failures: The first few cases that failed, for the run log. Deliberately capped:
            a run of any size has to hold a bounded amount in memory (architecture rule 2.3),
            and the complete record is ``_failures.jsonl``.
        failure_kinds: Every failure counted by exception type, however many there were.
        traffic: What the connector asked of its source, or ``None`` when it does not report
            it. Live while the run is in progress.
        cases_on_disk: Complete cases in the store once the run had finished, counted from
            the filesystem rather than from a counter.
        survey: What the store was found to contain once the run had finished, or ``None``
            when the run died before it could be walked. It is the manifest's ``contents``
            block, and the run log prints it, so an operator reads the same observed figures
            a citation would.
    """

    jurisdiction_code: str
    connector: str
    store_path: Path
    started_at: datetime
    mode: RunMode = RunMode.MIRROR
    finished_at: datetime | None = None
    status: IngestStatus = IngestStatus.RUNNING
    counters: MirrorCounters = field(default_factory=MirrorCounters)
    window_since: datetime | None = None
    window_until: datetime | None = None
    checkpoint_before: Checkpoint | None = None
    checkpoint_after: Checkpoint | None = None
    error_message: str | None = None
    limit: int | None = None
    failures: list[MirrorFailure] = field(default_factory=list)
    failure_kinds: Counter[str] = field(default_factory=Counter)
    traffic: SourceTraffic | None = None
    cases_on_disk: int | None = None
    survey: CorpusSurvey | None = None

    def summary(self) -> str:
        """Return the one-line summary the CLI prints.

        Returns:
            The jurisdiction, the outcome and the counters, in the order they happen.
        """
        counters = self.counters
        offered = "listed" if self.mode is RunMode.REPAIR else "discovered"
        return (
            f"{self.jurisdiction_code}: {self.mode.value} {self.status.value} - "
            f"{counters.discovered} {offered}, {counters.mirrored} mirrored, "
            f"{counters.skipped} already held, {counters.errors} failed, "
            f"{counters.bytes_written / 1_000_000:.1f} MB written to {self.store_path}"
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form recorded in the corpus manifest."""
        return {
            "mode": self.mode.value,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "status": self.status.value,
            "window_since": self.window_since.isoformat() if self.window_since else None,
            "window_until": self.window_until.isoformat() if self.window_until else None,
            "discovered": self.counters.discovered,
            "mirrored": self.counters.mirrored,
            "skipped": self.counters.skipped,
            "failed": self.counters.errors,
            "bytes_written": self.counters.bytes_written,
            "error": self.error_message,
        }


def mirror_jurisdiction(
    jurisdiction_code: str,
    since: datetime | None = None,
    until: datetime | None = None,
    *,
    settings: Settings | None = None,
    store_root: Path | None = None,
    limit: int | None = None,
    connector: SourceConnector | None = None,
) -> MirrorReport:
    """Mirror one jurisdiction's corpus to disk, resuming where the last run stopped.

    Args:
        jurisdiction_code: Jurisdiction to mirror, e.g. ``EU``.
        since: Lower bound of the window. ``None`` lets the stored checkpoint supply it,
            which is what makes a resumed capture pick up rather than start over.
        until: Upper bound. Pin it for a capture that is going to take days: it is what makes
            the corpus's edge a stated instant rather than "whenever each run happened to
            reach the end", and it is recorded in the manifest as such.
        settings: Validated settings. Defaults to the process-wide settings.
        store_root: Root of the case-law store. Defaults to ``corpus_store_dir``.
        limit: Stop after this many cases have been fetched. For a rehearsal; a full capture
            leaves it unset.
        connector: Connector to drive. Defaults to the one the registry holds for the
            jurisdiction; tests pass a fake.

    Returns:
        The run's report. Ownership of the connector passes to this function, which closes it
        however the run ends.

    Raises:
        ConnectorNotFoundError: If no connector serves the jurisdiction.
        CorpusStoreError: If the store cannot be opened or its position cannot be recorded.
    """

    def walk(
        source: SourceConnector,
        store: CorpusStore,
        report: MirrorReport,
        stop: StopRequest,
        resolved: Settings,
        cap: int | None,
    ) -> None:
        """Resolve the window from the stored position, then walk it."""
        report.checkpoint_before = store.read_checkpoint(source.name)
        report.window_since = resolve_since(since, report.checkpoint_before)
        log.info(
            "mirroring a corpus",
            extra={
                "context": {
                    "jurisdiction": report.jurisdiction_code,
                    "connector": report.connector,
                    "store": str(store.path),
                    "since": report.window_since.isoformat() if report.window_since else None,
                    "until": until.isoformat() if until else None,
                    "already_held": store.count_cases(),
                }
            },
        )
        _run(source, store, report, stop, resolved, cap)

    return run_against_store(
        jurisdiction_code,
        walk,
        mode=RunMode.MIRROR,
        until=until,
        settings=settings,
        store_root=store_root,
        limit=limit,
        connector=connector,
    )


#: What a run does once its store, connector and report are ready. It receives the connector,
#: the store, the report to accumulate into, the shutdown flag, the settings and the fetch
#: limit, and must leave a terminal status on the report. The capture and the repair differ
#: only in this callable, which is what keeps one set of scaffolding — the store, the signal
#: handler, the "write the log whatever happens" rule and the manifest — for both.
RunWork = Callable[
    [SourceConnector, CorpusStore, MirrorReport, StopRequest, Settings, int | None], None
]


def run_against_store(
    jurisdiction_code: str,
    work: RunWork,
    *,
    mode: RunMode,
    until: datetime | None = None,
    settings: Settings | None = None,
    store_root: Path | None = None,
    limit: int | None = None,
    connector: SourceConnector | None = None,
) -> MirrorReport:
    """Open a store, run one piece of work against it, and record what happened.

    Everything a run against the corpus store owes whatever it was doing: the connector is
    built and closed, the store is opened, ``SIGINT`` is trapped, and the run log is written
    on every path out — including the one where the process is dying, because a run that
    failed is exactly the run whose record matters (core document 2.7).

    Args:
        jurisdiction_code: Jurisdiction to run against, e.g. ``EU``.
        work: What to do once everything is ready; see :data:`RunWork`.
        mode: Whether this is a capture or a repair, recorded on the report so the log and the
            manifest can be read correctly.
        until: Upper bound the caller pinned, if any, recorded on the report.
        settings: Validated settings. Defaults to the process-wide settings.
        store_root: Root of the case-law store. Defaults to ``corpus_store_dir``.
        limit: Stop after this many cases have been fetched, or ``None``.
        connector: Connector to drive. Defaults to the one the registry holds for the
            jurisdiction; tests pass a fake.

    Returns:
        The run's report. Ownership of the connector passes to this function, which closes it
        however the run ends.

    Raises:
        ConnectorNotFoundError: If no connector serves the jurisdiction.
        CorpusStoreError: If the store cannot be opened or its position cannot be recorded.
    """
    resolved = settings if settings is not None else get_settings()
    started = datetime.now(UTC)
    with ExitStack() as stack:
        source = connector if connector is not None else connector_for(jurisdiction_code, resolved)
        stack.callback(source.close)
        store = CorpusStore(
            store_root if store_root is not None else resolved.corpus_store_dir,
            source.jurisdiction_code,
        )
        store.prepare()
        report = MirrorReport(
            jurisdiction_code=source.jurisdiction_code,
            connector=source.name,
            store_path=store.path,
            started_at=started,
            mode=mode,
            window_until=until,
            limit=limit,
            traffic=source.traffic,
        )
        stop = stack.enter_context(StopRequest())
        try:
            work(source, store, report, stop, resolved, limit)
        except BaseException as error:
            # Whatever ends a run - a full disk, a bug, a signal the handler did not take -
            # the record of it is the point. Written first, then the failure is re-raised
            # untouched for the CLI to turn into an exit code.
            report.status = IngestStatus.FAILED
            report.error_message = report.error_message or f"{type(error).__name__}: {error}"
            report.finished_at = datetime.now(UTC)
            report.cases_on_disk = store.count_cases()
            write_run_log(store, report, resolved)
            raise
        report.finished_at = datetime.now(UTC)
        _finish(store, report, resolved)
    return report


def rebuild_manifest(
    jurisdiction_code: str,
    *,
    settings: Settings | None = None,
    store_root: Path | None = None,
) -> tuple[Path, CorpusSurvey]:
    """Rewrite a store's manifest from the store, without running anything against a source.

    The ``contents`` block is re-observed and everything a run recorded — the capture window,
    the configuration, the totals, the run history — is carried forward exactly as it stands.
    Nothing is fetched, no connector is built and no request is sent, so this is safe to run
    against a corpus at any time, and it is how a store whose manifest was written by an
    earlier version of this code, or by a run that misdescribed it, is corrected once rather
    than left as folklore.

    Args:
        jurisdiction_code: Jurisdiction whose store is described, e.g. ``EU``.
        settings: Validated settings. Defaults to the process-wide settings.
        store_root: Root of the case-law store. Defaults to ``corpus_store_dir``.

    Returns:
        The manifest as written, and what the walk found.

    Raises:
        CorpusStoreError: If the jurisdiction has no directory under the store root — an empty
            manifest for a store that is not there would be a confident description of
            nothing — or if the manifest cannot be written.
    """
    resolved = settings if settings is not None else get_settings()
    store = CorpusStore(
        store_root if store_root is not None else resolved.corpus_store_dir, jurisdiction_code
    )
    if not store.path.is_dir():
        message = f"there is no {store.jurisdiction_code} store at {store.path} to describe"
        raise CorpusStoreError(message)
    previous = store.read_manifest()
    survey = store.survey()
    record = {
        "layout_version": _LAYOUT_VERSION,
        "jurisdiction": store.jurisdiction_code,
        "connector": previous.get("connector"),
        "contents": survey.as_dict(),
        "capture": dict(_mapping(previous.get("capture"))),
        "configuration": _previous_configuration(previous),
        "totals": _carried_totals(previous),
        "runs": list(previous["runs"]) if isinstance(previous.get("runs"), list) else [],
    }
    store.write_manifest(record)
    log.info(
        "corpus manifest rewritten from the store",
        extra={
            "context": {
                "jurisdiction": store.jurisdiction_code,
                "store": str(store.path),
                "cases": survey.cases,
                "resource_types": dict(survey.resource_types),
            }
        },
    )
    return store.path / _MANIFEST_NAME, survey


def _run(
    source: SourceConnector,
    store: CorpusStore,
    report: MirrorReport,
    stop: StopRequest,
    settings: Settings,
    limit: int | None,
) -> None:
    """Walk the window, mirroring what is not on disk yet.

    Args:
        source: The connector to drive.
        store: The store to write to.
        report: The report to accumulate into, so a run that ends badly still records what it
            had done by then.
        stop: The graceful-shutdown flag.
        settings: Validated settings, supplying how often the position is written.
        limit: Stop after this many cases have been fetched, or ``None``.
    """
    position = report.checkpoint_before or Checkpoint(
        connector=source.name, jurisdiction_code=report.jurisdiction_code
    )
    report.checkpoint_after = position
    every = settings.pipeline_batch_size
    holding_back = False
    since_written = 0
    try:
        for candidate in source.discover(report.window_since, report.window_until):
            if stop.requested:
                break
            report.counters.discovered += 1
            stored = mirror_case(source, store, candidate, report)
            if stored and not holding_back:
                position = position.advanced_to(
                    modified_at=candidate.modified_at,
                    cursor=candidate.cursor,
                    source_id=candidate.source_id,
                )
                report.checkpoint_after = position
            elif not stored:
                # The ingestion runner's rule: nothing after a failure is considered done, so
                # the window stays open and the case is offered again next run.
                holding_back = True
            since_written += 1
            if since_written >= every:
                store.write_checkpoint(position)
                since_written = 0
            if limit is not None and report.counters.mirrored >= limit:
                log.info("mirror limit reached", extra={"context": {"limit": limit}})
                break
    except SourceUnavailableError as error:
        report.status = IngestStatus.FAILED
        report.error_message = str(error)
        log.warning(
            "the source became unusable; the mirror stops where it is",
            extra={"context": {"jurisdiction": report.jurisdiction_code, "error": str(error)}},
        )
    else:
        report.status = final_status(report, stopped=stop.requested)
    store.write_checkpoint(position)


def mirror_case(
    source: SourceConnector,
    store: CorpusStore,
    candidate: Candidate,
    report: MirrorReport,
) -> bool:
    """Store one case, unless it is already held.

    The local half of this — ``store.holds`` — is what makes a repair possible at all: it is
    a stat call rather than a request, so a run may be offered a hundred thousand identifiers
    and pay only for the ones it does not have (``docs/architecture.md`` rule 2.10).

    Args:
        source: The connector to fetch through.
        store: The store to write to.
        candidate: The case to mirror.
        report: The report to count into.

    Returns:
        Whether the case is on disk when this returns — either because it already was, or
        because it was written now. ``False`` means the checkpoint must not advance past it.

    Raises:
        SourceUnavailableError: If the source as a whole has become unusable, which ends the
            run rather than this case.
    """
    try:
        if store.holds(candidate.source_id):
            report.counters.skipped += 1
            return True
    except CorpusStoreError as error:
        return _failed(store, report, candidate.source_id, error)
    try:
        raw = source.fetch(candidate)
    except SourceUnavailableError:
        raise
    except ConnectorError as error:
        return _failed(store, report, candidate.source_id, error)
    case = _normalise(source, raw)
    try:
        written, files = store.write_case(
            candidate.source_id, _payloads(raw, case), _metadata(source, candidate, raw, case)
        )
    except CorpusStoreError as error:
        return _failed(store, report, candidate.source_id, error)
    report.counters.mirrored += 1
    report.counters.bytes_written += written
    log.debug(
        "case mirrored",
        extra={
            "context": {
                "source_id": candidate.source_id,
                "files": [item.name for item in files],
                "bytes": written,
            }
        },
    )
    return True


def _normalise(source: SourceConnector, raw: RawDocument) -> NormalisedCase | None:
    """Map a fetched payload onto the schema, for the metadata file and the payload list.

    Args:
        source: The connector that fetched it.
        raw: The payload.

    Returns:
        The normalised case, or ``None`` when it could not be read. A mirror keeps the bytes
        whatever happens to them afterwards: a notice this project cannot parse today is
        exactly the one worth having on disk, so the failure downgrades the metadata rather
        than losing the case.
    """
    try:
        return source.normalise(raw)
    except ConnectorError as error:
        log.warning(
            "a fetched case could not be normalised; storing the source record alone",
            extra={"context": {"source_id": raw.source_id, "error": str(error)}},
        )
        return None


def _failed(store: CorpusStore, report: MirrorReport, source_id: str, error: Exception) -> bool:
    """Record one case that could not be stored.

    Args:
        store: The store, whose failure log the case is appended to.
        report: The report to count into.
        source_id: The case's identifier.
        error: What went wrong.

    Returns:
        ``False``, so the caller holds the checkpoint back.
    """
    report.counters.errors += 1
    kind = type(error).__name__
    report.failure_kinds[kind] += 1
    if len(report.failures) < _FAILURE_SAMPLE:
        report.failures.append(MirrorFailure(source_id=source_id, reason=f"{kind}: {error}"))
    store.record_failure(source_id, f"{kind}: {error}")
    log.warning(
        "case not mirrored; holding the checkpoint back",
        extra={"context": {"source_id": source_id, "error": str(error)}},
    )
    return False


def _metadata(
    source: SourceConnector,
    candidate: Candidate,
    raw: RawDocument,
    case: NormalisedCase | None,
) -> dict[str, Any]:
    """Build the index and provenance record stored beside a case's payloads.

    It is an *index*, not a second copy of the payloads: the fields a corpus is selected and
    sorted by, plus who fetched what, when and from where. Anything it leaves out — the
    citation graph, the parties, the body of the decision — is in the payloads next to it,
    verbatim, which is the whole reason they are kept.

    Args:
        source: The connector that supplied the case.
        candidate: The candidate it was discovered as.
        raw: The response it was fetched as.
        case: The normalised case, or ``None`` when it could not be read.

    Returns:
        The record, ready to be written as ``metadata.json``.
    """
    return {
        "identifier": candidate.source_id,
        "jurisdiction": candidate.jurisdiction_code,
        "connector": source.name,
        "source_system": case.source_system if case is not None else None,
        "normalised": case is not None,
        "title": case.title if case is not None else candidate.title,
        "subject": case.subject if case is not None else None,
        "language": case.language if case is not None else None,
        "decision_date": _iso(case.decision_date if case is not None else candidate.decision_date),
        "filing_date": _iso(case.filing_date) if case is not None else None,
        "publication_date": _iso(case.publication_date) if case is not None else None,
        "case_numbers": list(case.case_numbers) if case is not None else [],
        "procedure_type": case.procedure_type if case is not None else None,
        "court": _court(case),
        "source_url": (case.source_url if case is not None else None) or candidate.source_url,
        "fetched_at": raw.retrieved_at.isoformat(),
        "fetched_from": raw.source_url or candidate.source_url,
        "source_modified_at": _iso(candidate.modified_at),
        "source_revision": candidate.content_hash,
        "discovery_cursor": candidate.cursor,
        "source_metadata": dict(case.source_metadata) if case is not None else {},
    }


def _court(case: NormalisedCase | None) -> dict[str, Any] | None:
    """Return the deciding court, flattened for the metadata file.

    Args:
        case: The normalised case, or ``None``.

    Returns:
        The court's identifier, name and abbreviation, or ``None`` when the source named no
        court.
    """
    if case is None or case.court is None:
        return None
    return {
        "source_identifier": case.court.source_identifier,
        "name": case.court.name,
        "abbreviation": case.court.abbreviation,
        "source_type": case.court.source_type,
    }


def _iso(value: datetime | date | None) -> str | None:
    """Render a date or instant as ISO 8601.

    Args:
        value: The value, or ``None``.

    Returns:
        The ISO 8601 form, or ``None``.
    """
    return value.isoformat() if value is not None else None


def final_status(report: MirrorReport, *, stopped: bool) -> IngestStatus:
    """Decide how a run that reached the end of its list of cases should be recorded.

    Args:
        report: The run's report.
        stopped: Whether a shutdown was requested.

    Returns:
        ``interrupted`` when a signal ended it, ``partial`` when cases failed and ``success``
        otherwise. ``partial`` is not cosmetic in either mode: for a capture the window did
        not fully advance, for a repair the cases are still missing, and a scheduler has to be
        able to see both.
    """
    if stopped:
        return IngestStatus.INTERRUPTED
    return IngestStatus.PARTIAL if report.counters.errors else IngestStatus.SUCCESS


def _finish(store: CorpusStore, report: MirrorReport, settings: Settings) -> None:
    """Write the corpus manifest and the run's own log, and log what the run did.

    The store is surveyed first, and the survey is what supplies both the manifest's ``contents``
    block and the run's own count of what is on disk — so the two can never disagree, and
    neither of them is the run's opinion of what it fetched.

    Args:
        store: The store to write to.
        report: The finished run.
        settings: The validated settings the run used, recorded in the manifest.
    """
    report.survey = store.survey()
    report.cases_on_disk = report.survey.cases
    store.write_manifest(_manifest(store, report, settings, report.survey))
    write_run_log(store, report, settings)
    log.info(
        "mirror run finished",
        extra={
            "context": {
                "jurisdiction": report.jurisdiction_code,
                "status": report.status.value,
                **report.as_dict(),
            }
        },
    )


def _manifest(
    store: CorpusStore,
    report: MirrorReport,
    settings: Settings,
    survey: CorpusSurvey,
) -> dict[str, Any]:
    """Build the corpus manifest: what this corpus is, and when it was taken.

    Three blocks, and the difference between them is the point of the file.

    ``contents`` is **observed**: it is :meth:`CorpusStore.survey`, a walk of the disk, and it
    is what a reader may cite. ``capture`` is what the runs *asked* the source for, which is
    the window a methodology page states. ``configuration`` is what a run was *configured*
    with, which describes that process and nothing else — a run that was misconfigured, or
    that failed before it fetched anything, still has a configuration, and it is not a
    description of the corpus. Version 1 of this file called that block ``source`` and put it
    where scope was read from, which is how a store of a hundred thousand cases came to
    declare four resource types (rule 2.11).

    Args:
        store: The store, for what the previous manifest recorded.
        report: The run that just finished.
        settings: The validated settings the run used.
        survey: What the store was found to contain, counted from the store.

    Returns:
        The manifest, carrying forward what earlier runs of the same capture recorded.
    """
    previous = store.read_manifest()
    totals = _carried_totals(previous)
    history = previous.get("runs")
    earlier: list[Any] = list(history) if isinstance(history, list) else []
    runs = [*earlier, report.as_dict()][-_MANIFEST_RUN_HISTORY:]
    return {
        "layout_version": _LAYOUT_VERSION,
        "jurisdiction": report.jurisdiction_code,
        "connector": report.connector,
        "contents": survey.as_dict(),
        "capture": _capture_block(_mapping(previous.get("capture")), report),
        "configuration": _configuration_block(previous, settings, report),
        "totals": {
            "bytes_written": totals["bytes_written"] + report.counters.bytes_written,
            "runs": totals["runs"] + 1,
        },
        "runs": runs,
    }


def _carried_totals(previous: Mapping[str, Any]) -> dict[str, int]:
    """Return the cumulative run bookkeeping a previous manifest recorded.

    These two are the only figures in the file that are the *runs'* rather than the corpus's:
    how much this project's runs have written, and how many of them there have been. Version 1
    kept the case count here too; it is dropped rather than carried, because the number of
    cases is an observation, ``contents`` is where observations live, and one fact with two
    homes is how the staler of them comes to be quoted.

    Args:
        previous: The manifest already on disk.

    Returns:
        The carried totals, both zero when there was no readable manifest.
    """
    totals = _mapping(previous.get("totals"))
    return {
        "bytes_written": int(totals.get("bytes_written") or 0),
        "runs": int(totals.get("runs") or 0),
    }


def _capture_block(previous: Mapping[str, Any], report: MirrorReport) -> dict[str, Any]:
    """Build the manifest's statement of what this corpus is and when it was taken.

    A capture states its own window: that is what a methodology page cites. A **repair** does
    not, and must not overwrite one. It never walked a window — it compared an identifier
    listing with the disk — so a repair that rewrote ``window_since`` to its own bound, or to
    ``null``, would leave the corpus claiming an edge no run had ever reached. It therefore
    leaves the capture's own fields exactly as it found them and touches only ``updated_at``;
    what it did is in the ``runs`` history beside it, where it is labelled a repair.

    Args:
        previous: The ``capture`` block of the manifest already on disk, or an empty mapping.
        report: The run that just finished.

    Returns:
        The block to write.
    """
    touched = (report.finished_at or report.started_at).isoformat()
    started = previous.get("started_at") or report.started_at.isoformat()
    if report.mode is RunMode.REPAIR and previous:
        return {**previous, "started_at": started, "updated_at": touched}
    return {
        "started_at": started,
        "updated_at": touched,
        "window_since": report.window_since.isoformat() if report.window_since else None,
        "window_until": report.window_until.isoformat() if report.window_until else None,
        "status": report.status.value,
    }


def _configuration_block(
    previous: Mapping[str, Any], settings: Settings, report: MirrorReport
) -> dict[str, Any]:
    """Record the settings a run was launched with, and say whose they are.

    **A run that failed or was interrupted does not overwrite this.** It has no standing to
    redescribe how the corpus was taken: two runs launched without
    ``PLT_EURLEX_RESOURCE_TYPES`` set failed within minutes and left a hundred thousand cases
    described by the four-type default. The block therefore belongs to the last run that
    reached the end of its work, and a run that did not reach the end leaves it as it found it.

    A store that has never recorded one gets this run's, whatever became of the run, because a
    configuration nobody can read is worse than one labelled with the status of the run that
    wrote it — and ``recorded_by`` carries that status.

    Args:
        previous: The manifest already on disk, whose ``configuration`` — or, in a version 1
            file, ``source`` — is carried forward when this run may not replace it.
        settings: The validated settings this run used.
        report: The run that just finished.

    Returns:
        The block to write.
    """
    existing = _previous_configuration(previous)
    if existing and report.status not in _CAPTURED_SOMETHING:
        return existing
    return {
        "note": _CONFIGURATION_NOTE,
        "recorded_by": {
            "run_started_at": report.started_at.isoformat(),
            "mode": report.mode.value,
            "status": report.status.value,
        },
        "settings": _capture_configuration(settings, report.connector),
    }


def _previous_configuration(previous: Mapping[str, Any]) -> dict[str, Any]:
    """Read the configuration a previous manifest recorded, whichever layout wrote it.

    Args:
        previous: The manifest already on disk.

    Returns:
        The block, or an empty mapping when there is none. A version 1 ``source`` block is
        migrated into the current shape with ``recorded_by`` null: that layout did not record
        which run had written it, and inventing an answer here is exactly the kind of
        confident wrong figure this file now exists to prevent.
    """
    current = _mapping(previous.get("configuration"))
    if current:
        return dict(current)
    legacy = _mapping(previous.get("source"))
    if not legacy:
        return {}
    return {"note": _CONFIGURATION_NOTE, "recorded_by": None, "settings": dict(legacy)}


def _capture_configuration(settings: Settings, connector: str) -> dict[str, Any]:
    """Select the settings a run against this connector was launched with.

    Every source setting is named after the connector that reads it — ``eurlex_sparql_url``,
    ``rechtspraak_search_url`` — so the connector's own name selects them without this module
    learning a jurisdiction. The politeness settings go in beside them: they are not part of
    what was captured, but they are part of how the source was treated, which is the other
    thing an operator of a public endpoint may want to see.

    Args:
        settings: The validated settings the run used.
        connector: Connector name, e.g. ``eurlex``.

    Returns:
        The relevant settings. Source endpoints, window sizes and language preferences are
        exactly what a reader of the corpus needs to know; a secret is skipped outright, so
        a source that one day needs a credential cannot leak it into a published manifest.
    """
    prefix = f"{connector.lower()}_"
    selected: dict[str, Any] = {}
    for name in type(settings).model_fields:
        if not name.startswith(prefix):
            continue
        value = getattr(settings, name)
        if isinstance(value, SecretStr):
            continue
        selected[name] = value
    return {
        **selected,
        "user_agent": settings.user_agent(__version__),
        "requests_per_second": settings.http_requests_per_second,
    }
