"""The filter chain contract from ``docs/architecture.md`` section 4.

Stage 1 is the keyword matcher, but the chain itself must stay pluggable: a later stage has
to slot in behind the same ABC without a connector changing (``docs/core-document.md``
section 2.5, point 4). These tests pin that property with stages that have nothing to do
with keywords.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from plt.pipeline.filters.base import (
    Filter,
    FilterableDocument,
    FilterChain,
    FilterResult,
    TermMatch,
)


@dataclass
class StubCase:
    """A minimal document satisfying :class:`FilterableDocument`."""

    jurisdiction_code: str = "NL"
    title: str | None = None
    abstract: str | None = None
    subject: str | None = None
    full_text: str | None = None


@dataclass
class RecordingFilter(Filter):
    """A stage that records what it saw and returns a fixed verdict."""

    name: str = "recording"
    passed: bool = True
    seen: list[str] = field(default_factory=list)

    def evaluate(self, case: FilterableDocument) -> FilterResult:
        """Record the document and return the configured verdict."""
        self.seen.append(case.jurisdiction_code)
        return FilterResult(
            passed=self.passed,
            reason=f"{self.name} says {self.passed}",
            stage=self.name,
        )


def test_stub_case_satisfies_the_protocol() -> None:
    assert isinstance(StubCase(), FilterableDocument)


def test_empty_chain_passes_everything() -> None:
    result = FilterChain().evaluate(StubCase())

    assert result.passed
    assert result.matches == ()
    assert result.stage == "chain"


def test_chain_runs_every_stage_in_order_when_all_pass() -> None:
    first = RecordingFilter(name="first")
    second = RecordingFilter(name="second")

    result = FilterChain.of(first, second).evaluate(StubCase(jurisdiction_code="EU"))

    assert first.seen == ["EU"]
    assert second.seen == ["EU"]
    assert result.stage == "second"
    assert result.passed


def test_chain_short_circuits_on_the_first_rejection() -> None:
    first = RecordingFilter(name="first", passed=False)
    second = RecordingFilter(name="second")

    result = FilterChain.of(first, second).evaluate(StubCase())

    assert result.stage == "first"
    assert not result.passed
    assert second.seen == [], "a rejected document must not reach the next stage"


def test_a_stage_is_appended_without_mutating_the_original_chain() -> None:
    chain = FilterChain.of(RecordingFilter(name="first"))

    extended = chain.with_stage(RecordingFilter(name="second"))

    assert chain.stage_names == ["first"]
    assert extended.stage_names == ["first", "second"]
    assert len(extended) == 2
    assert [stage.name for stage in extended] == ["first", "second"]


def test_evaluate_all_streams_results_lazily() -> None:
    stage = RecordingFilter()
    chain = FilterChain.of(stage)
    cases = (StubCase(jurisdiction_code=code) for code in ("NL", "EU", "NL"))

    results = chain.evaluate_all(cases)

    assert stage.seen == [], "no document may be evaluated before the stream is consumed"
    assert next(results).passed
    assert stage.seen == ["NL"], "documents must be evaluated one at a time"
    assert [result.passed for result in results] == [True, True]


def test_filter_is_abstract() -> None:
    with pytest.raises(TypeError):
        Filter()  # type: ignore[abstract]


def test_results_and_matches_are_immutable() -> None:
    match = TermMatch(
        term_id="nl-glyfosaat",
        term="glyfosaat",
        category="active_substance",
        list_version="1.0.0",
        field="full_text",
        snippet="…glyfosaat…",
        matched_text="glyfosaat",
        occurrences=1,
        start=0,
        end=9,
    )
    result = FilterResult(passed=True, reason="ok", stage="keywords", matches=(match,))

    with pytest.raises(AttributeError):
        result.passed = False  # type: ignore[misc]
    with pytest.raises(AttributeError):
        match.category = "brand"  # type: ignore[misc]
