"""A fake source connector, and the helpers the pipeline tests build runs out of.

Unit tests never touch the network (``CONTRIBUTING.md`` section 4), and the pipeline core is
deliberately jurisdiction-agnostic, so every runner test drives it through this fake rather
than through Rechtspraak or CELLAR. The fake is also the honest test of the interface: if a
connector can be written against :class:`~plt.pipeline.base.SourceConnector` without the
runner learning anything about it, this file is the proof.

It records what it was asked for — which candidates were discovered, which documents were
fetched — so a test can assert that the deduplication pre-check really did prevent a
download rather than merely reach the same conclusion afterwards.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from plt.config import Settings
from plt.db.models import DocumentType
from plt.pipeline.base import (
    Candidate,
    DocumentUnavailableError,
    NormalisedCase,
    NormalisedCourt,
    NormalisedDocument,
    RawDocument,
    SourceConnector,
)

#: The instant the fake source's first document was modified.
EPOCH = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

#: Text that reaches ``min_score`` against the shipped Dutch list on its own: a weight-3
#: term at the full-text multiplier of 1.0.
PESTICIDE_TEXT = (
    "De rechtbank overweegt dat de toelating van gewasbeschermingsmiddelen door het Ctgb "
    "in geschil is en dat eiser onvoldoende heeft onderbouwd dat sprake is van schade."
)

#: Text with nothing a curated list looks for, so the filter chain rejects it.
UNRELATED_TEXT = (
    "Het geschil betreft de uitleg van een huurovereenkomst voor bedrijfsruimte en de "
    "vraag of de opzegtermijn in acht is genomen."
)


@dataclass
class FakeDocument:
    """One document the fake source publishes.

    Attributes:
        source_id: Identifier, standing in for an ECLI.
        modified_at: Source modification timestamp, driving the checkpoint.
        text: Full text of the (single) language version.
        title: Case title.
        abstract: Summary.
        subject: Subject-matter classification; the Dutch *rechtsgebied*.
        language: Language of the text.
        discovery_hash: Revision fingerprint discovery exposes, if this source does. When
            set, the runner can skip the document without fetching it.
        payload_suffix: Appended to the payload, so a test can simulate an upstream edit.
        extra_texts: Further language versions, as ``(language, text)`` pairs.
    """

    source_id: str
    modified_at: datetime = EPOCH
    text: str = PESTICIDE_TEXT
    title: str = "Uitspraak"
    abstract: str | None = None
    subject: str | None = None
    language: str = "nl"
    discovery_hash: str | None = None
    payload_suffix: str = ""
    extra_texts: tuple[tuple[str, str], ...] = ()


def documents(
    count: int,
    *,
    prefix: str = "ECLI:NL:RBTEST:2026:",
    text: str = PESTICIDE_TEXT,
    with_hash: bool = False,
) -> list[FakeDocument]:
    """Build a run of documents, one minute apart, oldest first.

    Args:
        count: How many to build.
        prefix: Identifier prefix.
        text: Full text every document carries.
        with_hash: Whether discovery exposes a revision fingerprint, which is what lets the
            pre-check skip a document without fetching it.

    Returns:
        The documents, in the order discovery yields them.
    """
    return [
        FakeDocument(
            source_id=f"{prefix}{index}",
            modified_at=EPOCH + timedelta(minutes=index),
            text=text,
            discovery_hash=f"discovery-hash-{index}" if with_hash else None,
        )
        for index in range(count)
    ]


class FakeConnector(SourceConnector):
    """A connector over an in-memory list of documents.

    Its constructor takes ``settings`` first and everything else by keyword, which is the
    shape :mod:`plt.pipeline.registry` builds every connector with.

    Attributes:
        docs: The documents the fake source holds, oldest first.
        discovered: Identifiers ``discover`` yielded, in order.
        fetched: Identifiers ``fetch`` was called for, in order — the evidence that the
            deduplication pre-check really did prevent a download.
        closed: Whether :meth:`close` has been called.
    """

    jurisdiction_code = "NL"
    name = "fake"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        docs: Sequence[FakeDocument] = (),
        fail_fetch: frozenset[str] = frozenset(),
        fail_normalise: frozenset[str] = frozenset(),
        raise_on_discover: Exception | None = None,
        on_fetch: Callable[[str], None] | None = None,
    ) -> None:
        """Build the fake.

        Args:
            settings: Settings, as the registry passes them.
            docs: The documents the fake source holds, oldest first.
            fail_fetch: Identifiers whose fetch raises, standing in for a document the
                source cannot serve.
            fail_normalise: Identifiers whose normalisation raises.
            raise_on_discover: Error raised from ``discover``, for the failed-run tests.
            on_fetch: Hook called with each identifier before it is fetched; a test uses it
                to raise a signal partway through a run.
        """
        super().__init__(settings)
        self.docs = docs
        self.fail_fetch = fail_fetch
        self.fail_normalise = fail_normalise
        self.raise_on_discover = raise_on_discover
        self.on_fetch = on_fetch
        self.discovered: list[str] = []
        self.fetched: list[str] = []
        self.closed = False

    def discover(self, since: datetime | None, until: datetime | None) -> Iterator[Candidate]:
        """Yield the documents in the window, oldest first, one at a time.

        Args:
            since: Inclusive lower bound on the modification timestamp.
            until: Inclusive upper bound on the modification timestamp.

        Yields:
            One candidate per document in the window.

        Raises:
            Exception: Whatever ``raise_on_discover`` holds, for the failure tests.
        """
        if self.raise_on_discover is not None:
            raise self.raise_on_discover
        for document in self.docs:
            if since is not None and document.modified_at < since:
                continue
            if until is not None and document.modified_at > until:
                continue
            self.discovered.append(document.source_id)
            yield Candidate(
                source_id=document.source_id,
                jurisdiction_code=self.jurisdiction_code,
                modified_at=document.modified_at,
                content_hash=document.discovery_hash,
                cursor=f"offset:{len(self.discovered)}",
                title=document.title,
                source_url=f"https://example.invalid/{document.source_id}",
            )

    def fetch(self, candidate: Candidate) -> RawDocument:
        """Return the stored payload for a candidate.

        Args:
            candidate: The candidate to fetch.

        Returns:
            The raw document.

        Raises:
            DocumentUnavailableError: If the identifier is in ``fail_fetch``.
        """
        if self.on_fetch is not None:
            self.on_fetch(candidate.source_id)
        self.fetched.append(candidate.source_id)
        if candidate.source_id in self.fail_fetch:
            message = f"the fake source refuses to serve {candidate.source_id}"
            raise DocumentUnavailableError(message)
        document = self._document(candidate.source_id)
        return RawDocument(
            candidate=candidate,
            payload=f"<uitspraak>{document.text}</uitspraak>{document.payload_suffix}",
            media_format="xml",
            source_url=candidate.source_url,
        )

    def normalise(self, raw: RawDocument) -> NormalisedCase:
        """Map a payload onto the schema.

        Args:
            raw: The payload returned by :meth:`fetch`.

        Returns:
            The normalised case, carrying one document per language version.

        Raises:
            DocumentUnavailableError: If the identifier is in ``fail_normalise``.
        """
        if raw.source_id in self.fail_normalise:
            message = f"the fake payload for {raw.source_id} cannot be parsed"
            raise DocumentUnavailableError(message)
        document = self._document(raw.source_id)
        versions = [
            NormalisedDocument(
                doc_type=DocumentType.JUDGMENT,
                language=document.language,
                full_text=document.text,
                raw_payload=raw.payload,
                media_format="xml",
            ),
            *(
                NormalisedDocument(
                    doc_type=DocumentType.JUDGMENT,
                    language=language,
                    full_text=text,
                    raw_payload=raw.payload,
                    media_format="xml",
                )
                for language, text in document.extra_texts
            ),
        ]
        return NormalisedCase(
            source_id=document.source_id,
            jurisdiction_code=self.jurisdiction_code,
            source_system="fake",
            title=document.title,
            abstract=document.abstract,
            subject=document.subject,
            decision_date=document.modified_at.date(),
            language=document.language,
            case_numbers=("12/3456",),
            source_url=raw.source_url,
            court=NormalisedCourt(source_identifier="fake-court", name="Rechtbank Test"),
            documents=tuple(versions),
        )

    def close(self) -> None:
        """Record that the runner released the connector."""
        self.closed = True

    def _document(self, source_id: str) -> FakeDocument:
        """Look one document up by identifier.

        Args:
            source_id: The identifier to look up.

        Returns:
            The document.

        Raises:
            DocumentUnavailableError: If the fake source does not hold it.
        """
        for document in self.docs:
            if document.source_id == source_id:
                return document
        message = f"the fake source has no document {source_id}"  # pragma: no cover
        raise DocumentUnavailableError(message)  # pragma: no cover
