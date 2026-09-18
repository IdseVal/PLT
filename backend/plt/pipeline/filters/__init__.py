"""Pluggable filter chain applied to normalised candidates.

Stage 1 is the legislation matcher in :mod:`plt.pipeline.filters.legislation`. Later stages
(classifier models, citation-based filters, manual review queues) plug in behind the same
ABC without touching the connectors (``docs/CORE_DOCUMENT.md`` section 2.5)::

    chain = FilterChain.of(LegislationFilter.for_jurisdiction("NL"))
    result = chain.evaluate(case)
"""

from __future__ import annotations

from plt.pipeline.filters.base import (
    Filter,
    FilterableDocument,
    FilterChain,
    FilterResult,
    TermMatch,
)
from plt.pipeline.filters.legislation import (
    Instrument,
    LegislationFilter,
    LegislationList,
    LegislationListError,
    LegislationListNotFoundError,
    LegislationListValidationError,
    load_legislation_list,
    load_legislation_list_for,
)

__all__ = [
    "Filter",
    "FilterChain",
    "FilterResult",
    "FilterableDocument",
    "Instrument",
    "LegislationFilter",
    "LegislationList",
    "LegislationListError",
    "LegislationListNotFoundError",
    "LegislationListValidationError",
    "TermMatch",
    "load_legislation_list",
    "load_legislation_list_for",
]
