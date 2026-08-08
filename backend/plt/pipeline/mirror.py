"""The corpus mirror: one jurisdiction's source payloads, on disk, verbatim.

A mirror is not an ingestion. It stores what a source served, unfiltered and unclassified,
so that everything downstream can be re-run over an **identical** corpus. That is what
``docs/core-document.md`` section 2.8 asks for and what a live endpoint cannot give: two
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
            _checkpoint.json           where the next run resumes
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
its capture window, the connector's configuration and its totals in ``manifest.json``, and
every failure in ``_failures.jsonl``. A published methodology has to be able to say what the
corpus is and when it was taken, and an absence nobody can audit is the expensive kind. On
top of those machine records, every run - including one that failed or was interrupted -
leaves one file a person can read in ``logs/``; see :mod:`plt.pipeline.runlog`, which exists
because this command is about to run weekly with nobody watching it.

One rule is inherited from the ingestion runner deliberately: **the checkpoint stops
advancing at the first case that failed**, so nothing after a failure is ever considered
done. A case that fails every run holds its window open and keeps appearing in the failure
count, which is how an operator finds out about it. Re-enumerating that window costs
discovery queries but no fetches, because everything already on disk is skipped.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
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
from plt.pipeline.runlog import LOG_DIR_NAME, write_run_log
from plt.utils.logging import get_logger
from plt.utils.shutdown import StopRequest

__all__ = [
    "CorpusStore",
    "CorpusStoreError",
    "MirrorCounters",
    "MirrorFailure",
    "MirrorReport",
    "StoredFile",
    "case_folder_name",
    "mirror_jurisdiction",
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
_FAILURES_NAME: Final[str] = "_failures.jsonl"
_RESERVED_FILES: Final[frozenset[str]] = frozenset(
    {
        _MANIFEST_NAME.lower(),
        _CHECKPOINT_NAME.lower(),
        _FAILURES_NAME.lower(),
        LOG_DIR_NAME.lower(),
    }
)

#: Version of the on-disk layout, recorded in the manifest so a reader of a store taken
#: today can tell whether it is the layout the code in front of them expects.
_LAYOUT_VERSION: Final[int] = 1

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
        """Read where the last run of this connector stopped.

        Args:
            connector: Connector name, which the stored position must match — a store
                repopulated by a different connector must not resume from the old one's
                cursor.

        Returns:
            The stored position, or ``None`` when there is none to resume from.
        """
        try:
            body = (self._path / _CHECKPOINT_NAME).read_bytes()
        except OSError:
            return None
        try:
            record = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.warning(
                "unreadable mirror checkpoint; starting from the beginning of the window",
                extra={"context": {"path": str(self._path / _CHECKPOINT_NAME)}},
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

    def write_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Store where the next run should resume.

        Written whole and replaced atomically, so a process killed here leaves either the old
        position or the new one and never half of either.

        Args:
            checkpoint: The position to store. It describes cases already on disk, so it is
                written whatever the outcome of the run — unlike the ingestion checkpoint,
                whose work can still be rolled back when a run fails.

        Raises:
            CorpusStoreError: If it cannot be written; a run that cannot record its position
                would silently start over.
        """
        record = {**checkpoint.as_dict(), "updated_at": datetime.now(UTC).isoformat()}
        try:
            _replace(self._path / _CHECKPOINT_NAME, _dump(record))
        except OSError as error:
            message = f"the mirror checkpoint could not be written to {self._path}: {error}"
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
            cite and a counter can only ever be what the code believed.
        """
        try:
            entries = list(self._path.iterdir())
        except OSError:
            return 0
        return sum(1 for entry in entries if (entry / _METADATA_NAME).is_file())

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
    """

    jurisdiction_code: str
    connector: str
    store_path: Path
    started_at: datetime
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

    def summary(self) -> str:
        """Return the one-line summary the CLI prints.

        Returns:
            The jurisdiction, the outcome and the counters, in the order they happen.
        """
        counters = self.counters
        return (
            f"{self.jurisdiction_code}: {self.status.value} - "
            f"{counters.discovered} discovered, {counters.mirrored} mirrored, "
            f"{counters.skipped} already held, {counters.errors} failed, "
            f"{counters.bytes_written / 1_000_000:.1f} MB written to {self.store_path}"
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form recorded in the corpus manifest."""
        return {
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
            window_until=until,
            limit=limit,
            traffic=source.traffic,
        )
        report.checkpoint_before = store.read_checkpoint(source.name)
        report.window_since = resolve_since(since, report.checkpoint_before)
        stop = stack.enter_context(StopRequest())
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
        try:
            _run(source, store, report, stop, resolved, limit)
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
            stored = _mirror_one(source, store, candidate, report)
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
        report.status = _final_status(report, stopped=stop.requested)
    store.write_checkpoint(position)


def _mirror_one(
    source: SourceConnector,
    store: CorpusStore,
    candidate: Candidate,
    report: MirrorReport,
) -> bool:
    """Store one case, unless it is already held.

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


def _final_status(report: MirrorReport, *, stopped: bool) -> IngestStatus:
    """Decide how a run that reached the end of its window should be recorded.

    Args:
        report: The run's report.
        stopped: Whether a shutdown was requested.

    Returns:
        ``interrupted`` when a signal ended it, ``partial`` when cases failed — the window
        did not fully advance, so a scheduler must be able to see it — and ``success``
        otherwise.
    """
    if stopped:
        return IngestStatus.INTERRUPTED
    return IngestStatus.PARTIAL if report.counters.errors else IngestStatus.SUCCESS


def _finish(store: CorpusStore, report: MirrorReport, settings: Settings) -> None:
    """Write the corpus manifest and the run's own log, and log what the run did.

    Args:
        store: The store to write to.
        report: The finished run.
        settings: The validated settings the run used, recorded in the manifest.
    """
    report.cases_on_disk = store.count_cases()
    store.write_manifest(_manifest(store, report, settings))
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


def _manifest(store: CorpusStore, report: MirrorReport, settings: Settings) -> dict[str, Any]:
    """Build the corpus manifest: what this corpus is, and when it was taken.

    The capture window is the whole point. A methodology page has to be able to state the
    instant a corpus was closed at and the configuration it was taken under, or a precision
    figure quoted against it means nothing a year later.

    Args:
        store: The store, for what the previous manifest recorded.
        report: The run that just finished, carrying the count of what is actually on disk.
        settings: The validated settings the run used.

    Returns:
        The manifest, carrying forward what earlier runs of the same capture recorded.
    """
    previous = store.read_manifest()
    capture = _mapping(previous.get("capture"))
    totals = _mapping(previous.get("totals"))
    history = previous.get("runs")
    earlier: list[Any] = list(history) if isinstance(history, list) else []
    runs = [*earlier, report.as_dict()][-_MANIFEST_RUN_HISTORY:]
    return {
        "layout_version": _LAYOUT_VERSION,
        "jurisdiction": report.jurisdiction_code,
        "connector": report.connector,
        "capture": {
            "started_at": capture.get("started_at") or report.started_at.isoformat(),
            "updated_at": (report.finished_at or report.started_at).isoformat(),
            "window_since": report.window_since.isoformat() if report.window_since else None,
            "window_until": report.window_until.isoformat() if report.window_until else None,
            "status": report.status.value,
        },
        "source": _capture_configuration(settings, report.connector),
        "totals": {
            "cases": report.cases_on_disk if report.cases_on_disk is not None else 0,
            "bytes_written": int(totals.get("bytes_written") or 0) + report.counters.bytes_written,
            "runs": int(totals.get("runs") or 0) + 1,
        },
        "runs": runs,
    }


def _capture_configuration(settings: Settings, connector: str) -> dict[str, Any]:
    """Record the settings that shaped what was captured.

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
