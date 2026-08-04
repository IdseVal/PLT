"""Memory stays flat across a long run — measured, not asserted by inspection.

``docs/architecture.md`` rule 2.3 forbids accumulating a corpus in memory or in one
transaction, and the issue asks for five thousand documents as the proof. The pipeline
streams: ``discover`` is a generator, batches are sliced off it, each batch is committed in
its own session, and no stage keeps a document once it is stored.

The experiment below runs 5,000 synthetic documents through the whole chain against a
temporary SQLite database, sampling :mod:`tracemalloc` every 1,000. Flat means the last
sample is no higher than the first by more than a small margin — not that allocation stops,
which no Python program can promise, but that nothing grows with the number of documents
processed. A pipeline that held the corpus would show 5,000 judgments' worth of growth here;
the margin is a fraction of that.
"""

from __future__ import annotations

import gc
import logging
import tracemalloc
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from plt.db.base import Base
from plt.db.models import (
    Case,
    DocumentType,
    IngestStatus,
    Jurisdiction,
    JurisdictionType,
)
from plt.db.session import create_session_factory, make_engine
from plt.pipeline.base import (
    Candidate,
    NormalisedCase,
    NormalisedDocument,
    RawDocument,
    SourceConnector,
)
from plt.pipeline.runner import run_jurisdiction
from plt.utils.logging import get_logger
from tests.conftest import build_settings
from tests.fakes import EPOCH, PESTICIDE_TEXT

log = get_logger(__name__)

#: Documents pushed through the pipeline. The issue names this figure.
DOCUMENT_COUNT = 5_000

#: How often the traced memory is sampled.
SAMPLE_EVERY = 1_000

#: Growth allowed between the first and last sample. Three judgments' worth of text, so a
#: pipeline that retained even a thousandth of the corpus would fail this.
GROWTH_LIMIT_BYTES = 3 * 1024 * 1024


class SamplingConnector(SourceConnector):
    """A connector that generates documents on the fly and samples memory as it goes.

    Generating rather than storing matters: a list of five thousand documents would itself
    be the thing under test. This yields each candidate as discovery reaches it, exactly as
    a paging source connector does, and keeps no reference to anything it has yielded.
    """

    jurisdiction_code = "NL"
    name = "sampling"

    def __init__(self, count: int) -> None:
        """Prepare a source of ``count`` documents.

        Args:
            count: How many documents the fake source publishes.
        """
        super().__init__(build_settings())
        self._count = count
        self.samples: list[tuple[int, int]] = []

    def discover(self, since: datetime | None, until: datetime | None) -> Iterator[Candidate]:
        """Yield candidates one at a time, sampling traced memory periodically.

        Args:
            since: Ignored; the whole synthetic corpus is in the window.
            until: Ignored.

        Yields:
            One candidate per synthetic document.
        """
        del since, until
        for index in range(self._count):
            if index % SAMPLE_EVERY == 0:
                gc.collect()
                self.samples.append((index, tracemalloc.get_traced_memory()[0]))
            yield Candidate(
                source_id=f"ECLI:NL:RBTEST:2026:{index}",
                jurisdiction_code="NL",
                modified_at=EPOCH + timedelta(seconds=index),
                cursor=f"offset:{index}",
                title=f"Uitspraak {index}",
            )

    def fetch(self, candidate: Candidate) -> RawDocument:
        """Build the payload for a candidate without keeping a copy.

        Args:
            candidate: The candidate to fetch.

        Returns:
            A raw document of roughly the size of a short judgment.
        """
        return RawDocument(
            candidate=candidate,
            payload=f"<uitspraak>{PESTICIDE_TEXT} nr {candidate.source_id}</uitspraak>",
            media_format="xml",
        )

    def normalise(self, raw: RawDocument) -> NormalisedCase:
        """Map the payload onto the schema.

        Args:
            raw: The payload returned by :meth:`fetch`.

        Returns:
            The normalised case.
        """
        return NormalisedCase(
            source_id=raw.source_id,
            jurisdiction_code=self.jurisdiction_code,
            source_system="sampling",
            title=raw.candidate.title,
            decision_date=raw.candidate.modified_at.date() if raw.candidate.modified_at else None,
            language="nl",
            documents=(
                NormalisedDocument(
                    doc_type=DocumentType.JUDGMENT,
                    language="nl",
                    full_text=raw.payload,
                    raw_payload=raw.payload,
                    media_format="xml",
                ),
            ),
        )


def test_memory_stays_flat_across_five_thousand_documents(tmp_path: Path) -> None:
    settings = build_settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'plt.db'}",
        pipeline_batch_size=50,
        pipeline_report_dir=tmp_path / "reports",
    )
    engine = make_engine(settings)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        session.add(Jurisdiction(code="NL", name="Netherlands", type=JurisdictionType.STATE))
        session.commit()

    connector = SamplingConnector(DOCUMENT_COUNT)
    # pytest keeps every captured log record for its failure report, so a run that logs one
    # line per case would show that growth as the pipeline's. Silence the pipeline's INFO
    # records for the measurement; what is being measured is the pipeline, not the harness.
    pipeline_logger = logging.getLogger("plt")
    previous_level = pipeline_logger.level
    pipeline_logger.setLevel(logging.WARNING)
    tracemalloc.start()
    try:
        gc.collect()
        report = run_jurisdiction(
            "NL", connector=connector, settings=settings, session_factory=factory
        )
        gc.collect()
        connector.samples.append((DOCUMENT_COUNT, tracemalloc.get_traced_memory()[0]))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
        pipeline_logger.setLevel(previous_level)
        engine.dispose()

    assert report.status is IngestStatus.SUCCESS
    assert report.counters.inserted == DOCUMENT_COUNT
    with factory() as session:
        assert session.execute(select(func.count()).select_from(Case)).scalar_one() == (
            DOCUMENT_COUNT
        )

    # Skip the first sample: it is taken before the first batch has allocated anything, so
    # it measures the empty pipeline rather than the steady state.
    steady = connector.samples[1:]
    baseline = steady[0][1]
    growth = [(index, current - baseline) for index, current in steady]
    log.info(
        "memory across the run",
        extra={
            "context": {
                "documents": DOCUMENT_COUNT,
                "peak_bytes": peak,
                "samples": list(connector.samples),
                "growth_over_baseline": growth,
            }
        },
    )

    assert max(delta for _, delta in growth) < GROWTH_LIMIT_BYTES
