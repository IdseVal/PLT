"""Filter interface and the values a filter stage returns.

``docs/architecture.md`` section 4 fixes the contract::

    class Filter(ABC):
        def evaluate(self, case: NormalisedCase) -> FilterResult: ...

The chain is pluggable by design (``docs/core-document.md`` section 2.5, point 4): a later
stage - a classifier, a citation filter, a manual review queue - is added by appending
another :class:`Filter` to a :class:`FilterChain`, never by touching a connector.

A stage reads text off the document and nothing else, so this module depends on the
structural :class:`FilterableDocument` protocol rather than on the concrete
``NormalisedCase`` that the connector work stream owns. ``NormalisedCase`` satisfies the
protocol by having the attributes; no import couples the two modules together.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = [
    "Filter",
    "FilterChain",
    "FilterResult",
    "FilterableDocument",
    "TermMatch",
]


@runtime_checkable
class FilterableDocument(Protocol):
    """The part of a normalised case a filter stage is allowed to look at.

    ``NormalisedCase`` (connector work stream) satisfies this structurally. Fields a
    jurisdiction's scoring block names but the document does not carry - ``subject``, for
    instance - are read with :func:`getattr` and treated as absent, so a source that has no
    such field needs no adapter.

    Attributes:
        jurisdiction_code: Jurisdiction the document belongs to, ``NL`` or ``EU``. Selects
            the keyword list.
        title: Case title, if the source provides one.
        abstract: Summary or headnote, if the source provides one.
        full_text: Full text of the decision, if it was fetched.
    """

    jurisdiction_code: str
    title: str | None
    abstract: str | None
    full_text: str | None


@dataclass(frozen=True, slots=True)
class TermMatch:
    """One curated term matching one field of one document.

    Rows of this shape populate the ``keyword_match`` table (``docs/architecture.md``
    section 3), which is how the content manager measures the precision and recall of a
    list against real runs. It is reporting output, not debug output.

    Occurrences of the same term - including its aliases - within the same field are
    aggregated into a single instance, so a term repeated five hundred times in a full text
    costs one row rather than five hundred. ``weight_applied`` always states the weight this
    instance contributed to the document score, which is ``0.0`` for a term whose
    ``requires`` gate did not open. The sum of ``weight_applied`` over a result's matches
    equals :attr:`FilterResult.score`.

    Attributes:
        term_id: Stable id of the curated term, e.g. ``nl-glyfosaat``. Aliases report the
            id of their parent term.
        list_version: Semantic version of the list that produced the match, so a stored
            match stays interpretable after the list is re-curated.
        field: Document field the term matched in, e.g. ``full_text``.
        weight_applied: Weight this match contributed to the score, after the field
            multiplier, ``count_term_once`` and any ``requires`` gate.
        snippet: Text surrounding the first occurrence, taken from the original document
            text with its diacritics and casing intact.
        matched_text: The first occurrence verbatim, so an alias or inflection is visible.
        occurrences: Number of times the term matched this field.
        start: Character offset of the first occurrence in the field, counted in the field's
            NFC-normalised text - which is the text itself for every source seen so far, and
            differs only for a source that emits decomposed characters.
        end: Character offset one past the first occurrence.
        gated: Whether a ``requires`` gate suppressed this term's weight.
    """

    term_id: str
    list_version: str
    field: str
    weight_applied: float
    snippet: str
    matched_text: str
    occurrences: int
    start: int
    end: int
    gated: bool = False


@dataclass(frozen=True, slots=True)
class FilterResult:
    """The verdict of one filter stage on one document.

    Attributes:
        passed: Whether the document survives this stage.
        score: Total weight accumulated, comparable against the list's ``min_score``.
        reason: Human-readable explanation for the pipeline report and the run log.
        stage: Name of the stage that produced the result.
        matches: Every term match found, contributing or not - see :class:`TermMatch`.
    """

    passed: bool
    score: float
    reason: str
    stage: str
    matches: tuple[TermMatch, ...] = ()


class Filter(ABC):
    """A stage in the filter chain.

    Stages are stateless with respect to documents: everything expensive - reading a
    curated list, compiling its patterns - happens once when the stage is constructed, so
    :meth:`evaluate` can be called across a whole run without repeating that work.
    """

    #: Short identifier of the stage, reported on every :class:`FilterResult`.
    name: str = "filter"

    @abstractmethod
    def evaluate(self, case: FilterableDocument) -> FilterResult:
        """Judge one document.

        Args:
            case: The normalised document to judge.

        Returns:
            The stage's verdict, carrying the score, the matches and a readable reason.
        """


@dataclass(frozen=True, slots=True)
class FilterChain:
    """An ordered, short-circuiting chain of filter stages.

    Stage 1 is the keyword matcher; further stages append behind it without any change to
    the connectors. The chain fails fast: the first stage that rejects a document ends the
    evaluation, because a later stage is generally the more expensive one.

    Attributes:
        stages: The stages, applied in order.
    """

    stages: tuple[Filter, ...] = field(default_factory=tuple)

    @classmethod
    def of(cls, *stages: Filter) -> FilterChain:
        """Build a chain from stages given positionally.

        Args:
            *stages: Stages to apply, in order.

        Returns:
            The chain.
        """
        return cls(stages=tuple(stages))

    def evaluate(self, case: FilterableDocument) -> FilterResult:
        """Run the chain over a document, stopping at the first rejection.

        Args:
            case: The normalised document to judge.

        Returns:
            The result of the stage that rejected the document, or the result of the last
            stage if every stage passed it. An empty chain passes everything, which keeps a
            dry run with no stages configured meaningful.
        """
        result = FilterResult(
            passed=True,
            score=0.0,
            reason="no filter stages configured",
            stage="chain",
        )
        for stage in self.stages:
            result = stage.evaluate(case)
            if not result.passed:
                return result
        return result

    def evaluate_all(self, cases: Iterable[FilterableDocument]) -> Iterator[FilterResult]:
        """Run the chain over a stream of documents.

        The stream is consumed lazily and results are yielded one at a time, so a run over a
        large corpus never holds more than one document's matches in memory.

        Args:
            cases: Documents to judge, in any order.

        Yields:
            One result per document, in the order the documents arrived.
        """
        for case in cases:
            yield self.evaluate(case)

    def with_stage(self, stage: Filter) -> FilterChain:
        """Return a new chain with one further stage appended.

        Args:
            stage: The stage to append.

        Returns:
            A new chain; the receiver is left unchanged.
        """
        return FilterChain(stages=(*self.stages, stage))

    def __len__(self) -> int:
        """Return the number of stages in the chain."""
        return len(self.stages)

    def __iter__(self) -> Iterator[Filter]:
        """Iterate over the stages in application order."""
        return iter(self.stages)

    @property
    def stage_names(self) -> Sequence[str]:
        """Return the names of the stages, in application order."""
        return [stage.name for stage in self.stages]
