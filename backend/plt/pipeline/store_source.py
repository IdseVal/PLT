"""Ingest from the corpus store instead of from the source.

The mirror already holds every payload the sources served, verbatim (``plt mirror``). Filtering
and classifying that corpus is a decision about text we have, so asking the courts for it again
would be a million requests spent re-reading our own disk — the cheapest-request rule
(``docs/architecture.md`` rule 2.10) at its plainest.

What this module adds is one adapter. :class:`StoredCorpusConnector` wraps a real connector and
replaces only the two stages that touch the network — discovery and fetching — with reads from
the store. Normalisation is the wrapped connector's own, unchanged: both shipped connectors
derive everything from the payload they were handed, so a payload read off disk normalises into
exactly the case the network run would have produced. That is the property this rests on, and it
is why the adapter must never reimplement a connector's mapping. A jurisdiction added later
inherits this for free, on the same terms.

A run from the store is a backfill, not a window. It reports the source's own connector name, so
the ingest checkpoint it leaves behind is the one the weekly network run resumes from: the
high-water mark ends on the newest case the corpus holds, and the next scheduled run asks the
source only for what has changed since.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

from plt.config import Settings, get_settings
from plt.pipeline.base import (
    Candidate,
    DocumentUnavailableError,
    NormalisedCase,
    RawDocument,
    SourceConnector,
    SourceTraffic,
)
from plt.pipeline.mirror import CorpusStore
from plt.pipeline.registry import connector_for
from plt.utils.logging import get_logger

log = get_logger(__name__)

#: Role a case's own source response is stored under, in ``metadata.json``'s file list.
_SOURCE_RECORD = "source_record"

#: Role a language version of the decision is stored under.
_FULL_TEXT = "full_text"

#: File name stem the source response is written to. Only needed for the folders the Dutch
#: corpus arrived with, which predate the file list and therefore state nothing about
#: themselves; every folder the mirror wrote names its files explicitly.
_LEGACY_SOURCE_RECORD = "raw_content"

#: Metadata keys that may carry the source's modification instant, newest format first.
_MODIFIED_KEYS = ("source_modified_at", "modified")

#: Metadata keys that may carry the decision date, newest format first.
_DECISION_DATE_KEYS = ("decision_date", "date")


class StoredCorpusConnector(SourceConnector):
    """Serve one jurisdiction's cases from the corpus store, over no network at all.

    Not registered, and deliberately: the registry answers "what serves this jurisdiction",
    and the answer to that is the source. This is built explicitly, by
    :func:`stored_corpus_connector`, when a caller has decided to read the mirror instead.
    """

    def __init__(
        self,
        inner: SourceConnector,
        *,
        store_root: Path | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Bind the adapter to a connector and to the store holding that connector's corpus.

        Args:
            inner: The connector whose corpus this reads and whose normalisation it uses.
                Ownership passes here: :meth:`close` closes it.
            store_root: Root of the case-law store. Defaults to ``corpus_store_dir``.
            settings: Validated settings. Defaults to the process-wide settings.
        """
        super().__init__(settings if settings is not None else get_settings())
        self._inner = inner
        root = store_root if store_root is not None else self.settings.corpus_store_dir
        self._store = CorpusStore(root, inner.jurisdiction_code)

    @property
    def store_path(self) -> Path:
        """Return the folder the cases are read from."""
        return self._store.path

    @property
    def traffic(self) -> SourceTraffic | None:
        """Return the requests this run sent the source, which is none.

        Reported rather than left unanswered: "no requests" is the fact worth recording about
        a run off local disk, and the default ``None`` would file it as "not measured".
        """
        return SourceTraffic()

    def discover(self, since: datetime | None, until: datetime | None) -> Iterator[Candidate]:
        """Yield one candidate per case on disk, streaming, in the filesystem's order.

        The order is not the source's. That is safe here because the checkpoint's timestamp is
        a high-water mark rather than a position — it only ever moves forward, so a full pass
        lands on the newest case the corpus holds however the folders came back. What it costs
        is mid-run resumption by timestamp, which a store run does not need: an interrupted run
        is simply run again, and deduplication skips what was already stored without reading a
        payload.

        Args:
            since: Only yield cases modified at or after this instant, or ``None``.
            until: Only yield cases modified at or before this instant, or ``None``.

        Yields:
            One candidate per readable case folder.
        """
        for directory, metadata in self._store.iter_cases():
            candidate = self._candidate(directory, metadata)
            if candidate is None:
                continue
            if not _within(candidate.modified_at, since, until):
                continue
            yield candidate

    def _candidate(self, directory: Path, metadata: Mapping[str, Any]) -> Candidate | None:
        """Describe one stored case as a candidate.

        Args:
            directory: The case folder, named in the warning when the record is unusable.
            metadata: The folder's ``metadata.json``.

        Returns:
            The candidate, or ``None`` when the record names no case — a folder that cannot
            say which case it holds is skipped and logged rather than ending the run.
        """
        source_id = _text(metadata.get("identifier"))
        if not source_id:
            log.warning(
                "a stored case names no identifier; skipping it",
                extra={"context": {"path": str(directory)}},
            )
            return None
        return Candidate(
            source_id=source_id,
            jurisdiction_code=self.jurisdiction_code,
            modified_at=_first_instant(metadata, _MODIFIED_KEYS),
            # The revision identifier the source published, as discovery recorded it. Carrying
            # it keeps the pre-fetch check in the same hash space the network run used, so a
            # second pass over the corpus skips unchanged cases without opening a payload.
            content_hash=_text(metadata.get("source_revision")),
            title=_text(metadata.get("title")),
            decision_date=_first_date(metadata, _DECISION_DATE_KEYS),
            source_url=_text(metadata.get("source_url")),
        )

    def fetch(self, candidate: Candidate) -> RawDocument:
        """Read one case's payloads off disk, in the shape the connector's fetch returns.

        Args:
            candidate: The candidate to read.

        Returns:
            The stored source response, carrying any full-text versions stored beside it in
            the ``manifestations`` the EU connector's normalisation reads. A jurisdiction that
            stores only the one payload — the Dutch one — yields an empty list here, which is
            what its normalisation already expects.

        Raises:
            DocumentUnavailableError: If the folder is gone, unreadable, or holds no source
                response. The runner treats that as one failed document, which is right: a
                corpus with a hole in it should not stop the other million cases.
        """
        directory = self._store.case_dir(candidate.source_id)
        metadata = _read_metadata(directory, candidate.source_id)
        files = _files(metadata, directory)
        record = next((item for item in files if item.get("role") == _SOURCE_RECORD), None)
        if record is None:
            message = f"{candidate.source_id}: {directory} holds no source record"
            raise DocumentUnavailableError(message)
        return RawDocument(
            candidate=candidate,
            payload=_read_text(directory, str(record.get("name") or ""), candidate.source_id),
            media_format=_text(record.get("format")),
            source_url=_text(record.get("source_url")),
            source_metadata={
                "notice_content_type": _text(record.get("content_type")),
                "manifestations": self._manifestations(directory, metadata, files, candidate),
                # Named so that anything reading a case's provenance can tell a run that read
                # the mirror from one that asked the source (architecture rule 2.9).
                "read_from_store": str(directory),
            },
        )

    def _manifestations(
        self,
        directory: Path,
        metadata: Mapping[str, Any],
        files: Sequence[Mapping[str, Any]],
        candidate: Candidate,
    ) -> list[dict[str, Any]]:
        """Rebuild the language versions a fetch would have returned alongside the notice.

        The stored file list says what each payload is and where it came from; the case's own
        ``retrieved_languages`` says which language the source called it and why that one was
        chosen. They are joined on the URL both record, so a version keeps the source's
        three-letter code rather than a code inferred here.

        Args:
            directory: The case folder.
            metadata: The folder's ``metadata.json``.
            files: The folder's file list.
            candidate: The case being read, for the log line.

        Returns:
            One entry per stored full text, in the order they were written.
        """
        stored = _mapping(metadata.get("source_metadata")).get("retrieved_languages")
        by_url = {
            _text(entry.get("source_url")): entry
            for entry in _records(stored)
            if _text(entry.get("source_url"))
        }
        versions: list[dict[str, Any]] = []
        for item in files:
            if item.get("role") != _FULL_TEXT:
                continue
            name = str(item.get("name") or "")
            try:
                payload = _read_text(directory, name, candidate.source_id)
            except DocumentUnavailableError as error:
                # One unreadable language version is not a case worth losing: the notice and
                # any other version still describe it, and the gap is on the record here.
                log.warning(
                    "a stored language version could not be read; leaving it out",
                    extra={
                        "context": {
                            "source_id": candidate.source_id,
                            "file": name,
                            "error": str(error),
                        }
                    },
                )
                continue
            source_url = _text(item.get("source_url"))
            described = by_url.get(source_url, {})
            versions.append(
                {
                    "language": _text(described.get("language")) or _text(item.get("language")),
                    "selected_as": _text(described.get("selected_as")),
                    "payload": payload,
                    "media_format": _text(item.get("format")),
                    "source_url": source_url,
                    "content_type": _text(item.get("content_type")),
                }
            )
        return versions

    def normalise(self, raw: RawDocument) -> NormalisedCase:
        """Map the stored payloads with the wrapped connector's own normalisation.

        Args:
            raw: The payloads read from the store.

        Returns:
            The case, exactly as a run against the source would have produced it.

        Raises:
            DocumentUnavailableError: If the stored payload cannot be mapped, which means the
                store holds something the connector no longer understands.
        """
        return self._inner.normalise(raw)

    def close(self) -> None:
        """Close the wrapped connector, whose client this adapter never used."""
        self._inner.close()


def stored_corpus_connector(
    jurisdiction_code: str,
    *,
    store_root: Path | None = None,
    settings: Settings | None = None,
) -> StoredCorpusConnector:
    """Build the store-backed connector for a jurisdiction.

    The jurisdiction code and connector name are the wrapped connector's, so a run off the
    store writes the same ``ingest_run.connector`` and advances the same checkpoint as a run
    against the source. That is the intended reading: the cases are the source's either way,
    and where a given run read them from is recorded per document rather than by renaming the
    source.

    Args:
        jurisdiction_code: Jurisdiction to read, e.g. ``NL``. Case-insensitive.
        store_root: Root of the case-law store. Defaults to ``corpus_store_dir``.
        settings: Validated settings. Defaults to the process-wide settings.

    Returns:
        A connector reading that jurisdiction's corpus from disk.

    Raises:
        ConnectorNotFoundError: If no connector serves the jurisdiction. Reading a corpus
            still needs the connector that captured it, to normalise what it holds.
    """
    resolved = settings if settings is not None else get_settings()
    inner = connector_for(jurisdiction_code, resolved)
    # jurisdiction_code and name are class attributes on SourceConnector - the registry keys on
    # them - so the adapter takes the wrapped connector's identity by being given a class that
    # declares it, rather than by assigning over a class variable per instance.
    subclass = type(
        f"Stored{type(inner).__name__}",
        (StoredCorpusConnector,),
        {"jurisdiction_code": inner.jurisdiction_code, "name": inner.name},
    )
    built = cast(type[StoredCorpusConnector], subclass)
    return built(inner, store_root=store_root, settings=resolved)


def _read_metadata(directory: Path, source_id: str) -> Mapping[str, Any]:
    """Read a case folder's ``metadata.json``.

    Args:
        directory: The case folder.
        source_id: The case being read, for the message.

    Returns:
        The parsed record.

    Raises:
        DocumentUnavailableError: If the file is missing or unreadable.
    """
    try:
        body = (directory / "metadata.json").read_bytes()
    except OSError as error:
        message = f"{source_id}: {directory} could not be read: {error}"
        raise DocumentUnavailableError(message) from error
    try:
        record = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        message = f"{source_id}: the metadata in {directory} is unreadable: {error}"
        raise DocumentUnavailableError(message) from error
    if not isinstance(record, dict):
        message = f"{source_id}: the metadata in {directory} is not a record"
        raise DocumentUnavailableError(message)
    return cast(Mapping[str, Any], record)


def _files(metadata: Mapping[str, Any], directory: Path) -> list[dict[str, Any]]:
    """Describe the payload files in a case folder.

    Folders the mirror wrote list their own files. The Dutch corpus predates that list, so a
    folder without one is described from what is on disk instead: a single source response,
    under the name the corpus has always used.

    Args:
        metadata: The folder's ``metadata.json``.
        directory: The case folder, listed when the record describes no files.

    Returns:
        One record per payload file, source record first.
    """
    described = _records(metadata.get("files"))
    if described:
        return described
    found = sorted(directory.glob(f"{_LEGACY_SOURCE_RECORD}.*"))
    return [
        {
            "name": path.name,
            "role": _SOURCE_RECORD,
            "format": path.suffix.lstrip(".").lower() or None,
            "language": _text(metadata.get("language")),
            "source_url": _text(metadata.get("source_url")),
        }
        for path in found[:1]
    ]


def _read_text(directory: Path, name: str, source_id: str) -> str:
    """Read one payload file as text.

    Args:
        directory: The case folder.
        name: File name within it.
        source_id: The case being read, for the message.

    Returns:
        The payload, decoded as the UTF-8 the store writes.

    Raises:
        DocumentUnavailableError: If the file is missing, unreadable, or not the text it was
            stored as.
    """
    if not name:
        message = f"{source_id}: a stored payload in {directory} has no file name"
        raise DocumentUnavailableError(message)
    try:
        return (directory / name).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        message = f"{source_id}: {directory / name} could not be read: {error}"
        raise DocumentUnavailableError(message) from error


def _within(moment: datetime | None, since: datetime | None, until: datetime | None) -> bool:
    """Return whether an instant falls inside a window.

    Args:
        moment: The case's modification instant, or ``None`` when it records none.
        since: Inclusive lower bound, or ``None``.
        until: Inclusive upper bound, or ``None``.

    Returns:
        Whether to ingest the case. A case with no instant is always ingested: the store is
        a corpus rather than a feed, and dropping what cannot be dated would silently narrow
        a backfill to the cases whose metadata happened to carry a timestamp.
    """
    if moment is None:
        return True
    if since is not None and moment < since:
        return False
    return not (until is not None and moment > until)


def _first_instant(metadata: Mapping[str, Any], keys: Sequence[str]) -> datetime | None:
    """Return the first readable instant among several metadata keys.

    Args:
        metadata: The record to read.
        keys: Keys to try, in order of preference.

    Returns:
        The instant in UTC, or ``None`` when none of the keys holds one.
    """
    for key in keys:
        moment = _instant(metadata.get(key))
        if moment is not None:
            return moment
    return None


def _first_date(metadata: Mapping[str, Any], keys: Sequence[str]) -> date | None:
    """Return the first readable date among several metadata keys.

    Args:
        metadata: The record to read.
        keys: Keys to try, in order of preference.

    Returns:
        The date, or ``None`` when none of the keys holds one.
    """
    for key in keys:
        value = _text(metadata.get(key))
        if not value:
            continue
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            continue
    return None


def _instant(value: object) -> datetime | None:
    """Read an ISO 8601 timestamp, as UTC.

    Args:
        value: The stored value, of whatever type the record happened to hold.

    Returns:
        The instant in UTC, or ``None`` when the value is not one. A timestamp stored without
        an offset — as the Dutch corpus's older folders hold it — is read as UTC rather than
        discarded, so those cases still carry a position.
    """
    text = _text(value)
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _text(value: object) -> str | None:
    """Return a non-empty string, or ``None``.

    Args:
        value: The stored value.

    Returns:
        The trimmed string, or ``None`` when it is blank or not a string.
    """
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _mapping(value: object) -> Mapping[str, Any]:
    """Return a mapping, or an empty one.

    Args:
        value: The stored value.

    Returns:
        The mapping, or ``{}`` when the value is not one.
    """
    return cast(Mapping[str, Any], value) if isinstance(value, dict) else {}


def _records(value: object) -> list[dict[str, Any]]:
    """Return a list of mappings, dropping anything that is not one.

    Args:
        value: The stored value.

    Returns:
        The records, or an empty list.
    """
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
