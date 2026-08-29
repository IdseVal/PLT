"""Pluggable filter chain applied to normalised candidates.

Stage 1 is the keyword matcher in :mod:`plt.pipeline.filters.keywords`. Later stages
(classifier models, citation-based filters, manual review queues) plug in behind the same
ABC without touching the connectors (``docs/CORE_DOCUMENT.md`` section 2.5)::

    chain = FilterChain.of(KeywordFilter.for_jurisdiction("NL"))
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
from plt.pipeline.filters.keywords import (
    KeywordFilter,
    KeywordList,
    KeywordListError,
    KeywordListNotFoundError,
    KeywordListValidationError,
    KeywordTerm,
    load_keyword_list,
    load_keyword_list_for,
)

__all__ = [
    "Filter",
    "FilterChain",
    "FilterResult",
    "FilterableDocument",
    "KeywordFilter",
    "KeywordList",
    "KeywordListError",
    "KeywordListNotFoundError",
    "KeywordListValidationError",
    "KeywordTerm",
    "TermMatch",
    "load_keyword_list",
    "load_keyword_list_for",
]
