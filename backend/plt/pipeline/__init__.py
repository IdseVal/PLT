"""Ingestion pipeline: connectors, filter chain, deduplication and checkpointing.

Per-jurisdiction run order (``docs/architecture.md`` section 4)::

    discover -> dedup pre-check -> fetch -> normalise -> filter chain -> persist -> checkpoint

Every stage streams: candidates are yielded one at a time, work is committed per batch, and
a checkpoint is written so an interrupted run resumes instead of restarting.
"""

from __future__ import annotations
