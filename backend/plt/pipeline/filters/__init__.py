"""Pluggable filter chain applied to normalised candidates.

Stage 1 is the keyword matcher in :mod:`plt.pipeline.filters.keywords`. Later stages
(classifier models, citation-based filters, manual review queues) plug in behind the same
ABC without touching the connectors (``docs/core-document.md`` section 2.5).
"""

from __future__ import annotations
