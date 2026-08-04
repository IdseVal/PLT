"""Ingestion pipeline: connectors, filter chain, deduplication and checkpointing.

Per-jurisdiction run order (``docs/architecture.md`` section 4)::

    discover -> dedup pre-check -> fetch -> normalise -> filter chain -> persist -> checkpoint

Every stage streams: candidates are yielded one at a time, work is committed per batch, and
a checkpoint is written so an interrupted run resumes instead of restarting.

Onboarding a jurisdiction is two files and no edits anywhere else: a
:class:`~plt.pipeline.base.SourceConnector` subclass under
:mod:`plt.pipeline.connectors` — discovered automatically by
:mod:`plt.pipeline.registry` — and a keyword list under ``data/keywords/``::

    from plt.pipeline import Candidate, NormalisedCase, PoliteClient, RawDocument
    from plt.pipeline import SourceConnector

    class BelgiumConnector(SourceConnector):
        jurisdiction_code = "BE"
        name = "juportal"

        def discover(self, since, until): ...
        def fetch(self, candidate): ...
        def normalise(self, raw): ...

    # and then, from anywhere:
    report = run_jurisdiction("BE")
"""

from __future__ import annotations

from plt.pipeline.base import (
    Candidate,
    ConnectorError,
    ConnectorNotFoundError,
    DocumentUnavailableError,
    NormalisedCase,
    NormalisedCitation,
    NormalisedCourt,
    NormalisedDocument,
    NormalisedParty,
    PipelineError,
    RawDocument,
    SourceConnector,
    SourceUnavailableError,
)
from plt.pipeline.checkpoint import Checkpoint, read_checkpoint, write_checkpoint
from plt.pipeline.dedup import DedupAction, DedupDecision, content_hash
from plt.pipeline.filters import Filter, FilterChain, FilterResult, KeywordFilter, TermMatch
from plt.pipeline.http import PoliteClient
from plt.pipeline.registry import available_jurisdictions, connector_for, register_connector
from plt.pipeline.report import MatchReport
from plt.pipeline.runner import IngestCounters, IngestReport, run_jurisdiction

__all__ = [
    "Candidate",
    "Checkpoint",
    "ConnectorError",
    "ConnectorNotFoundError",
    "DedupAction",
    "DedupDecision",
    "DocumentUnavailableError",
    "Filter",
    "FilterChain",
    "FilterResult",
    "IngestCounters",
    "IngestReport",
    "KeywordFilter",
    "MatchReport",
    "NormalisedCase",
    "NormalisedCitation",
    "NormalisedCourt",
    "NormalisedDocument",
    "NormalisedParty",
    "PipelineError",
    "PoliteClient",
    "RawDocument",
    "SourceConnector",
    "SourceUnavailableError",
    "TermMatch",
    "available_jurisdictions",
    "connector_for",
    "content_hash",
    "read_checkpoint",
    "register_connector",
    "run_jurisdiction",
    "write_checkpoint",
]
