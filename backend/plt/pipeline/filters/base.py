"""Filter interface and the values a filter stage returns.

``docs/architecture.md`` section 4 fixes the contract::

    class Filter(ABC):
        def evaluate(self, case: NormalisedCase) -> FilterResult: ...

The chain is pluggable by design (``docs/CORE_DOCUMENT.md`` section 2.5, point 4): a later
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
from dataclasses import dataclass, field, replace
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

    :class:`plt.pipeline.base.NormalisedCase` satisfies this structurally; no import couples
    the two modules together. A field a jurisdiction's scoring block names but a document
    does not carry is read with :func:`getattr` and treated as absent, so a source without
    an abstract needs no adapter.

    The members are declared read-only, which is what lets a document *compute* one of them.
    ``full_text`` is the case in point: the schema keeps full texts on ``case_document``, one
    row per language (``docs/architecture.md`` section 3), so a case may carry several, while
    a filter stage wants one text to scan. ``NormalisedCase.full_text`` is therefore a
    property joining its language versions. Declaring these as plain variables would make
    the protocol demand settable attributes and reject that property; a stage only ever reads
    them, so read-only is both accurate and permissive.

    Attributes:
        jurisdiction_code: Jurisdiction the document belongs to, ``NL`` or ``EU``. Selects
            the keyword list.
        title: Case title, if the source provides one.
        abstract: Summary or headnote, if the source provides one.
        subject: Subject-matter classification - the *rechtsgebied* for the Netherlands, the
            subject-matter heading for the EU. Both shipped lists weight it, the EU list
            above plain full text, because it is a curated signal rather than prose.
        full_text: Full text of the decision, if it was fetched. Where a case has several
            language versions this is all of them, joined.
    """

    @property
    def jurisdiction_code(self) -> str:
        """Return the jurisdiction code the document belongs to."""
        ...

    @property
    def title(self) -> str | None:
        """Return the case title, if the source provides one."""
        ...

    @property
    def abstract(self) -> str | None:
        """Return the summary or headnote, if the source provides one."""
        ...

    @property
    def subject(self) -> str | None:
        """Return the subject-matter classification, if the source provides one."""
        ...

    @property
    def full_text(self) -> str | None:
        """Return the full text of the decision, if it was fetched."""
        ...


@dataclass(frozen=True, slots=True)
class TermMatch:
    """One curated term matching one field of one document.

    Rows of this shape populate the ``keyword_match`` table (``docs/architecture.md``
    section 3), which is how the content manager measures the precision and recall of a
    list against real runs. It is reporting output, not debug output.

    Occurrences of the same term - including its aliases - within the same field are
    aggregated into a single instance, so a term repeated five hundred times in a full text
    costs one row rather than five hundred.

    A match is also a **label**. Selection is a word search, so a term that matched is a term
    the public is told about: :attr:`term` and :attr:`category` are what a case is listed
    under and what the case list is filtered by. That is why a gated match is marked rather
    than dropped - it is evidence for the curator - and why only ungated matches label a case.

    Attributes:
        term_id: Stable id of the curated term, e.g. ``nl-glyfosaat``. Aliases report the
            id of their parent term.
        term: The curated term as written in the list - not the inflection found in the
            text. This is the public label, so every case matching an alias of ``glyfosaat``
            is listed under ``glyfosaat`` and not under six spellings of it.
        category: The term's category, e.g. ``active_substance``. The second public label.
        list_version: Semantic version of the list that produced the match, so a stored
            match stays interpretable after the list is re-curated.
        field: Document field the term matched in, e.g. ``full_text``.
        snippet: Text surrounding the first occurrence, taken from the original document
            text with its diacritics and casing intact.
        matched_text: The first occurrence verbatim, so an alias or inflection is visible.
        occurrences: Number of times the term matched this field.
        start: Character offset of the first occurrence in the field, counted in the field's
            NFC-normalised text - which is the text itself for every source seen so far, and
            differs only for a source that emits decomposed characters.
        end: Character offset one past the first occurrence.
        gated: Whether a ``requires`` gate held this term back. A gated match selects
            nothing and labels nothing.
    """

    term_id: str
    term: str
    category: str
    list_version: str
    field: str
    snippet: str
    matched_text: str
    occurrences: int
    start: int
    end: int
    gated: bool = False


@dataclass(frozen=True, slots=True)
class FilterResult:
    """The verdict of one filter stage on one document.

    *Passed* decides whether the document enters the database at all, and selection is a word
    search: one curated term matching is enough, because a term that could not carry a case on
    its own does not belong in the list (``docs/CORE_DOCUMENT.md`` section 2.5). Precision is
    bought in curation, by removing the term, rather than in arithmetic.

    *Needs review* survives as the content manager's own flag. Nothing raises it
    automatically any more: a threshold is what made a document "borderline", and there is no
    longer a threshold to be near.

    Attributes:
        passed: Whether the document survives this stage.
        reason: Human-readable explanation for the pipeline report and the run log.
        stage: Name of the stage that produced the result.
        matches: Every term match found, gated or not - see :class:`TermMatch`.
        needs_review: Whether a content manager should look at this document. Never ``True``
            on a rejection: a document that did not pass is not in the database and there is
            nothing to curate.
    """

    passed: bool
    reason: str
    stage: str
    matches: tuple[TermMatch, ...] = ()
    needs_review: bool = False

    @property
    def labels(self) -> tuple[TermMatch, ...]:
        """Return one match per distinct term that actually selected this document.

        A term found in both the title and the full text is one label, not two, and a term
        whose ``requires`` gate stayed shut is not a label at all. The order is the order the
        terms were found in, so a report reads the way the document does.

        Returns:
            The labelling matches, at most one per term id.
        """
        seen: dict[str, TermMatch] = {}
        for match in self.matches:
            if not match.gated and match.term_id not in seen:
                seen[match.term_id] = match
        return tuple(seen.values())

    @property
    def matched_term_count(self) -> int:
        """Return how many distinct curated terms selected this document."""
        return len(self.labels)


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

        A review flag raised by *any* stage survives to the returned result, even though the
        result itself is the last stage's. A flag is a statement about the document, not
        about the stage that noticed, and a chain that dropped it as soon as a further stage
        were appended would quietly empty the review queue.

        Args:
            case: The normalised document to judge.

        Returns:
            The result of the stage that rejected the document, or the result of the last
            stage if every stage passed it. An empty chain passes everything, which keeps a
            dry run with no stages configured meaningful.
        """
        result = FilterResult(
            passed=True,
            reason="no filter stages configured",
            stage="chain",
        )
        needs_review = False
        for stage in self.stages:
            result = stage.evaluate(case)
            if not result.passed:
                return result
            needs_review = needs_review or result.needs_review
        if needs_review and not result.needs_review:
            return replace(result, needs_review=True)
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
