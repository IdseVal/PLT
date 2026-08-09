"""The connector interface: the data classes a jurisdiction is onboarded through.

Two of these tests pin decisions that were open when the filter chain was merged, and both
are contracts other work streams build on:

* a case carries one ``case_document`` per language, so :attr:`NormalisedCase.full_text`
  joins them — a term in any language version qualifies the case;
* ``subject`` is a field of the protocol, so the multiplier both shipped keyword lists give
  it actually applies.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from plt.db.models import DocumentType
from plt.pipeline.base import (
    Candidate,
    IdentifierListUnavailableError,
    NormalisedCase,
    NormalisedDocument,
    RawDocument,
    SourceConnector,
)
from plt.pipeline.filters.base import FilterableDocument
from plt.pipeline.filters.keywords import KeywordFilter
from tests.conftest import build_settings
from tests.fakes import PESTICIDE_TEXT, FakeConnector, FakeDocument


def case(**overrides: object) -> NormalisedCase:
    """Build a normalised case with sensible defaults."""
    fields: dict[str, object] = {
        "source_id": "ECLI:NL:RBTEST:2026:1",
        "jurisdiction_code": "NL",
        "source_system": "fake",
    }
    fields.update(overrides)
    return NormalisedCase(**fields)  # type: ignore[arg-type]


def test_a_normalised_case_satisfies_the_filter_protocol() -> None:
    assert isinstance(case(), FilterableDocument)


def test_full_text_is_none_without_documents() -> None:
    assert case().full_text is None


def test_full_text_ignores_documents_without_text() -> None:
    subject = case(
        documents=(
            NormalisedDocument(doc_type=DocumentType.ATTACHMENT, raw_payload="<pdf/>"),
            NormalisedDocument(doc_type=DocumentType.JUDGMENT, language="nl", full_text="tekst"),
        )
    )

    assert subject.full_text == "tekst"
    assert subject.text_languages == ("nl",)


def test_a_single_document_is_passed_through_without_copying() -> None:
    text = "een lange uitspraak"
    subject = case(documents=(NormalisedDocument(language="nl", full_text=text),))

    assert subject.full_text is text


def test_full_text_joins_language_versions_with_the_case_language_first() -> None:
    subject = case(
        language="fr",
        documents=(
            NormalisedDocument(language="en", full_text="english text"),
            NormalisedDocument(language="fr", full_text="texte francais"),
            NormalisedDocument(language="de", full_text="deutscher text"),
        ),
    )

    # The case's own language leads; the rest follow in a stable order, so the text a filter
    # stage sees never depends on the order the connector happened to build the tuple in.
    assert subject.full_text == "texte francais\n\ndeutscher text\n\nenglish text"
    assert subject.text_languages == ("en", "fr", "de")


def test_a_term_in_any_language_version_qualifies_the_case() -> None:
    """The EU publishes one judgment in many languages; the list covers several of them."""
    stage = KeywordFilter.for_jurisdiction("EU", settings=build_settings())
    subject = case(
        jurisdiction_code="EU",
        source_id="62026CJ0001",
        language="en",
        documents=(
            NormalisedDocument(language="en", full_text="A dispute about lease agreements."),
            NormalisedDocument(language="fr", full_text="Litige sur un produit phytosanitaire."),
        ),
    )

    result = stage.evaluate(subject)

    assert result.passed
    assert {match.field for match in result.matches} == {"full_text"}


def test_the_subject_field_is_scored() -> None:
    """``subject`` is the rechtsgebied for NL: on its own it reaches min_score."""
    stage = KeywordFilter.for_jurisdiction("NL", settings=build_settings())
    subject = case(subject="Gewasbeschermingsmiddelen en biociden")

    result = stage.evaluate(subject)

    assert result.passed
    assert {match.field for match in result.matches if match.weight_applied > 0} == {"subject"}


def test_the_eu_list_weights_the_subject_field_above_full_text() -> None:
    stage = KeywordFilter.for_jurisdiction("EU", settings=build_settings())

    in_subject = stage.evaluate(case(jurisdiction_code="EU", subject="Pesticides"))
    in_text = stage.evaluate(
        case(
            jurisdiction_code="EU",
            documents=(NormalisedDocument(language="en", full_text="Pesticides."),),
        )
    )

    # The EU list gives subject 1.2 and full text 1.0, so the same term in the
    # subject-matter heading counts for a fifth more than in the prose.
    assert in_text.score > 0
    assert in_subject.score == pytest.approx(in_text.score * 1.2)


def test_a_candidate_rejects_a_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Candidate(
            source_id="ECLI:NL:RBTEST:2026:1",
            jurisdiction_code="NL",
            modified_at=datetime(2026, 1, 1, 12, 0),  # noqa: DTZ001 - the point of the test
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("source_id", "  "), ("jurisdiction_code", "")],
)
def test_a_candidate_rejects_a_blank_identifier(field_name: str, value: str) -> None:
    fields = {"source_id": "ECLI:NL:RBTEST:2026:1", "jurisdiction_code": "NL", field_name: value}

    with pytest.raises(ValueError, match="must not be blank"):
        Candidate(**fields)  # type: ignore[arg-type]


def test_a_normalised_case_rejects_a_blank_source_system() -> None:
    with pytest.raises(ValueError, match="source_system must not be blank"):
        case(source_system=" ")


def test_a_raw_document_reports_its_size_in_bytes() -> None:
    candidate = Candidate(source_id="ECLI:NL:RBTEST:2026:1", jurisdiction_code="NL")
    raw = RawDocument(candidate=candidate, payload="één")

    assert raw.source_id == "ECLI:NL:RBTEST:2026:1"
    assert raw.byte_size == 5


def test_a_connector_is_a_context_manager_that_closes_itself() -> None:
    connector = FakeConnector(docs=[FakeDocument(source_id="ECLI:NL:RBTEST:2026:1")])

    with connector as opened:
        assert opened is connector

    assert connector.closed


def test_a_connector_that_cannot_list_cheaply_says_so_rather_than_walking() -> None:
    """A repair that fell back to discovery would cost more than the walk it replaces."""

    class Silent(FakeConnector):
        """A connector that inherits the default listing, i.e. offers none."""

        name = "silent"

    connector = Silent(docs=[FakeDocument(source_id="ECLI:NL:RBTEST:2026:1")])

    with pytest.raises(IdentifierListUnavailableError, match="without walking discovery"):
        list(connector.enumerate_identifiers())


def test_the_fake_connector_implements_the_interface() -> None:
    connector = FakeConnector(docs=[FakeDocument(source_id="ECLI:NL:RBTEST:2026:1")])

    assert isinstance(connector, SourceConnector)
    candidates = list(connector.discover(None, None))
    normalised = connector.normalise(connector.fetch(candidates[0]))

    assert normalised.full_text == PESTICIDE_TEXT
