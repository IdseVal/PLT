"""Filter stage 1: the curated keyword matcher.

The matcher decides what enters the database (``docs/core-document.md`` section 2.5), so
these tests exercise the semantics the curated lists rely on - every ``match`` mode,
``aliases``, ``requires``, ``exclusions``, the per-field multipliers and diacritic folding -
against the two shipped lists and against synthetic lists written into ``tmp_path``.

The shipped lists are content-manager-owned data. Nothing here edits them; the synthetic
lists exist precisely so that no test ever has a reason to.
"""

from __future__ import annotations

import json
import shutil
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from plt.pipeline.filters.base import FilterResult
from plt.pipeline.filters.keywords import (
    KeywordFilter,
    KeywordList,
    KeywordListError,
    KeywordListNotFoundError,
    KeywordListValidationError,
    fold_diacritics,
    load_keyword_list,
    load_keyword_list_for,
)
from tests.conftest import REPO_ROOT, build_settings

KEYWORDS_DIR = REPO_ROOT / "data" / "keywords"
SCHEMA_PATH = KEYWORDS_DIR / "schema.json"

#: Enough text to stand in for a real judgment without a fixture file.
BOILERPLATE = (
    "De rechtbank overweegt dat het bestuursorgaan in redelijkheid tot het besluit heeft "
    "kunnen komen, gelet op de belangen van partijen en de overgelegde stukken. Naar het "
    "oordeel van de rechtbank is het beroep ongegrond en blijft het besluit in stand, met "
    "veroordeling van eiser in de proceskosten. "
)


@dataclass
class Doc:
    """A normalised case as far as a filter stage is concerned."""

    jurisdiction_code: str = "NL"
    title: str | None = None
    abstract: str | None = None
    full_text: str | None = None
    subject: str | None = None


def term(
    term_id: str,
    text: str,
    weight: float,
    **overrides: Any,  # noqa: ANN401 - passes schema fields straight through
) -> dict[str, Any]:
    """Build one term entry for a synthetic list."""
    entry: dict[str, Any] = {
        "id": term_id,
        "term": text,
        "lang": "nl",
        "category": "general",
        "weight": weight,
    }
    entry.update(overrides)
    return entry


def make_list(terms: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    """Build a minimal schema-valid list document around a set of terms."""
    document: dict[str, Any] = {
        "schema_version": "1.0.0",
        "jurisdiction": "NL",
        "jurisdiction_name": "Netherlands",
        "list_version": "9.9.9",
        "updated": "2026-08-03",
        "languages": ["nl"],
        "scoring": {
            "min_score": 3,
            "count_term_once": True,
            "fields": {"title": 2.0, "abstract": 1.5, "full_text": 1.0},
        },
        "terms": terms,
    }
    document.update(overrides)
    return document


def write_list(tmp_path: Path, document: dict[str, Any], name: str = "nl.json") -> Path:
    """Write a synthetic list next to a copy of the real schema."""
    shutil.copy(SCHEMA_PATH, tmp_path / "schema.json")
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def build_filter(tmp_path: Path, document: dict[str, Any]) -> KeywordFilter:
    """Compile a synthetic list into a filter stage."""
    return KeywordFilter(load_keyword_list(write_list(tmp_path, document)))


def matched(result: FilterResult) -> set[str]:
    """Return the ids of the terms that contributed weight."""
    return {match.term_id for match in result.matches if match.weight_applied > 0}


# ----------------------------------------------------------------------------------------
# Loading and validation
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["nl", "eu"])
def test_shipped_lists_load_and_validate(code: str) -> None:
    path = KEYWORDS_DIR / f"{code}.json"
    keyword_list = load_keyword_list(path)
    # Derived from the file rather than hard-coded: these lists are curated by the content
    # manager, and a term added or split must not break the matcher's tests.
    term_count = len(json.loads(path.read_text(encoding="utf-8"))["terms"])

    assert keyword_list.jurisdiction == code.upper()
    assert keyword_list.term_count == term_count
    assert keyword_list.min_score > 0
    assert set(keyword_list.field_multipliers) >= {"title", "abstract", "full_text"}
    assert keyword_list.pattern_count > keyword_list.term_count, "aliases must be compiled too"


def test_shipped_lists_are_reached_through_the_settings() -> None:
    settings = build_settings()

    for code in ("NL", "EU"):
        keyword_list = load_keyword_list_for(code, settings)
        assert keyword_list.jurisdiction == code
        assert keyword_list.source_path == settings.keyword_list_path(code)


def test_a_list_is_compiled_once_per_process() -> None:
    settings = build_settings()

    first = load_keyword_list_for("NL", settings)
    second = load_keyword_list_for("NL", settings)

    assert first is second, "patterns must be compiled once per list, never per document"


def test_a_missing_list_names_the_jurisdiction(tmp_path: Path) -> None:
    settings = build_settings(keywords_dir=tmp_path)

    with pytest.raises(KeywordListNotFoundError, match="'ZZ'"):
        load_keyword_list_for("ZZ", settings)


def test_an_unparsable_list_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "nl.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(KeywordListValidationError, match="not valid JSON"):
        load_keyword_list(path, schema_path=SCHEMA_PATH)


def test_an_invalid_term_is_named_in_the_error(tmp_path: Path) -> None:
    document = make_list(
        [
            term("nl-good", "glyfosaat", 3),
            term("nl-broken", "iets", 3, category="not-a-category"),
        ]
    )

    with pytest.raises(KeywordListValidationError, match="nl-broken"):
        load_keyword_list(write_list(tmp_path, document))


def test_a_negative_weight_is_named_in_the_error(tmp_path: Path) -> None:
    document = make_list([term("nl-negative", "iets", -1)])

    with pytest.raises(KeywordListValidationError, match=r"nl-negative.*terms/0/weight"):
        load_keyword_list(write_list(tmp_path, document))


def test_duplicate_term_ids_are_rejected(tmp_path: Path) -> None:
    document = make_list([term("nl-same", "eerste", 3), term("nl-same", "tweede", 3)])

    with pytest.raises(KeywordListValidationError, match=r"nl-same.*duplicate"):
        load_keyword_list(write_list(tmp_path, document))


def test_a_gate_on_an_unknown_term_is_rejected(tmp_path: Path) -> None:
    document = make_list([term("nl-gated", "drift", 1, requires=["nl-missing"])])

    with pytest.raises(KeywordListValidationError, match=r"nl-gated.*nl-missing"):
        load_keyword_list(write_list(tmp_path, document))


def test_a_term_may_not_gate_itself(tmp_path: Path) -> None:
    document = make_list([term("nl-self", "drift", 1, requires=["nl-self"])])

    with pytest.raises(KeywordListValidationError, match=r"nl-self.*itself"):
        load_keyword_list(write_list(tmp_path, document))


def test_an_uncompilable_regex_term_is_named(tmp_path: Path) -> None:
    document = make_list([term("nl-bad-regex", "gewas(", 3, match="regex")])

    with pytest.raises(KeywordListValidationError, match=r"nl-bad-regex.*invalid regular"):
        load_keyword_list(write_list(tmp_path, document))


def test_a_missing_schema_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "nl.json"
    path.write_text(json.dumps(make_list([term("nl-x", "glyfosaat", 3)])), encoding="utf-8")

    with pytest.raises(KeywordListError, match="schema not found"):
        load_keyword_list(path, schema_path=tmp_path / "absent.json")


def test_a_missing_list_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(KeywordListNotFoundError, match="core document"):
        load_keyword_list(tmp_path / "nl.json", schema_path=SCHEMA_PATH)


def test_a_violation_outside_the_terms_array_is_located(tmp_path: Path) -> None:
    document = make_list([term("nl-x", "glyfosaat", 3)])
    del document["scoring"]

    with pytest.raises(KeywordListValidationError, match=r"<root>.*scoring"):
        load_keyword_list(write_list(tmp_path, document))


def test_a_blank_alias_is_rejected(tmp_path: Path) -> None:
    document = make_list([term("nl-blank", "glyfosaat", 3, aliases=["  "])])

    with pytest.raises(KeywordListValidationError, match=r"nl-blank.*empty"):
        load_keyword_list(write_list(tmp_path, document))


def test_a_stage_is_built_for_a_jurisdiction_through_the_settings() -> None:
    stage = KeywordFilter.for_jurisdiction("NL", settings=build_settings())

    assert stage.keyword_list.jurisdiction == "NL"
    assert stage.evaluate(Doc(full_text="glyfosaat")).passed


# ----------------------------------------------------------------------------------------
# The acceptance criteria, against the shipped Dutch list
# ----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def nl_list() -> KeywordList:
    """Return the shipped Dutch list, compiled once for the module."""
    return load_keyword_list(KEYWORDS_DIR / "nl.json")


@pytest.fixture(scope="module")
def nl_filter(nl_list: KeywordList) -> KeywordFilter:
    """Stage 1 bound to the shipped Dutch list."""
    return KeywordFilter(nl_list)


def test_glyfosaat_alone_qualifies_a_document(nl_filter: KeywordFilter) -> None:
    result = nl_filter.evaluate(Doc(full_text=f"{BOILERPLATE} Het gaat om glyfosaat."))

    assert result.passed
    assert result.score >= 3
    assert "nl-glyfosaat" in matched(result)


def test_lelieteelt_alone_does_not_qualify_a_document(nl_filter: KeywordFilter) -> None:
    result = nl_filter.evaluate(Doc(full_text=f"{BOILERPLATE} Het gaat om de lelieteelt."))

    assert not result.passed
    assert result.score < 3
    assert matched(result) == {"nl-lelieteelt"}


def test_lelieteelt_with_bespuiting_qualifies_a_document(nl_filter: KeywordFilter) -> None:
    result = nl_filter.evaluate(
        Doc(full_text=f"{BOILERPLATE} De lelieteelt en de bespuiting van het perceel.")
    )

    assert result.passed
    assert matched(result) == {"nl-lelieteelt", "nl-bespuiting"}


def test_drift_alone_scores_nothing(nl_filter: KeywordFilter) -> None:
    result = nl_filter.evaluate(
        Doc(full_text=f"{BOILERPLATE} Verdachte handelde in een vlaag van drift.")
    )

    assert not result.passed
    assert result.score == 0.0
    gated = {match.term_id for match in result.matches if match.gated}
    assert gated == {"nl-drift"}, "the homonym must be reported as disarmed, not dropped"


def test_drift_with_a_spraying_term_scores(nl_filter: KeywordFilter) -> None:
    result = nl_filter.evaluate(
        Doc(full_text=f"{BOILERPLATE} De drift bij de bespuiting van het perceel.")
    )

    assert result.passed
    assert matched(result) == {"nl-drift", "nl-bespuiting"}
    assert not any(match.gated for match in result.matches)


def test_an_exclusion_vetoes_a_document_that_would_otherwise_pass(
    nl_filter: KeywordFilter,
) -> None:
    passing = f"{BOILERPLATE} Er is glyfosaat aangetroffen op het perceel."
    assert nl_filter.evaluate(Doc(full_text=passing)).passed

    result = nl_filter.evaluate(
        Doc(full_text=f"{passing} Verdachte sloeg in een opwelling van drift.")
    )

    assert not result.passed
    assert result.score == 0.0
    assert "opwelling van drift" in result.reason
    assert "Criminal-law idiom" in result.reason


# ----------------------------------------------------------------------------------------
# The four documented exceptions of docs/jurisdictions/nl.md 5.4-5.7 (issue #57). Each one
# fixes a reproduction from the June 2026 dry run, and each is paired with the recall it was
# chosen to keep: an exception is a deliberate false negative (core document 2.7, 2.10), so a
# test that only proved the false positive gone would be testing half of the decision.
# ----------------------------------------------------------------------------------------


def test_the_toxicology_boilerplate_no_longer_admits_a_homicide_judgment(
    nl_filter: KeywordFilter,
) -> None:
    screen = (
        "In het toxicologisch onderzoek werden geen aanwijzingen gevonden voor de "
        "aanwezigheid van geneesmiddelen, drugs en/of bestrijdingsmiddelen."
    )

    result = nl_filter.evaluate(Doc(full_text=f"{BOILERPLATE} {screen}"))

    assert not result.passed
    assert result.score == 0.0


def test_the_boilerplate_guard_suppresses_one_occurrence_and_not_the_term(
    nl_filter: KeywordFilter,
) -> None:
    screen = (
        "In het bloed werden geen aanwijzingen gevonden voor de aanwezigheid van "
        "geneesmiddelen, drugs en/of bestrijdingsmiddelen."
    )
    elsewhere = "In de maaginhoud is het bestrijdingsmiddel parathion aangetroffen."

    result = nl_filter.evaluate(Doc(full_text=f"{BOILERPLATE} {screen} {elsewhere}"))

    assert result.passed, "the guard must disarm the enumeration, not the term"
    assert matched(result) == {"nl-bestrijdingsmiddel"}
    for plain in ("het gebruik van bestrijdingsmiddelen", "bestrijdingsmiddelengebruik"):
        assert nl_filter.evaluate(Doc(full_text=f"{BOILERPLATE} {plain}")).passed


def test_kwekerij_no_longer_matches_inside_hennepkwekerij(nl_filter: KeywordFilter) -> None:
    hennep = nl_filter.evaluate(
        Doc(full_text=f"{BOILERPLATE} In de loods is een hennepkwekerij aangetroffen.")
    )
    bare = nl_filter.evaluate(
        Doc(full_text=f"{BOILERPLATE} Op het perceel wordt een kwekerij geëxploiteerd.")
    )
    compound = nl_filter.evaluate(Doc(full_text=f"{BOILERPLATE} De boomkwekerijen aldaar."))

    assert hennep.score == 0.0
    assert matched(bare) == {"nl-boomkwekerij"}, "the bare word still carries its weight"
    assert matched(compound) == {"nl-boomkwekerij"}, "and so does the compound it was named for"


def test_ctb_no_longer_matches_cement_bound_road_base(nl_filter: KeywordFilter) -> None:
    road_base = nl_filter.evaluate(
        Doc(full_text=f"{BOILERPLATE} De aannemer heeft een CTB-laag aangebracht.")
    )
    historical = nl_filter.evaluate(
        Doc(full_text=f"{BOILERPLATE} Het CTB heeft de toelating destijds verlengd.")
    )
    current = nl_filter.evaluate(
        Doc(full_text=f"{BOILERPLATE} Het Ctgb heeft het middel toegelaten.")
    )

    assert not road_base.passed
    assert road_base.score == 0.0
    assert historical.passed, "the historical abbreviation is kept; only the collision is removed"
    assert current.passed


def test_toelatingsbesluit_alone_no_longer_qualifies_an_immigration_judgment(
    nl_filter: KeywordFilter,
) -> None:
    immigration = nl_filter.evaluate(
        Doc(full_text=f"{BOILERPLATE} Eiser komt op tegen het toelatingsbesluit van de minister.")
    )
    authorisation = nl_filter.evaluate(
        Doc(
            full_text=(
                f"{BOILERPLATE} Het beroep richt zich tegen het toelatingsbesluit over de "
                "toelating van gewasbeschermingsmiddelen."
            )
        )
    )

    assert not immigration.passed
    assert immigration.score == 0.0
    gated = {match.term_id for match in immigration.matches if match.gated}
    assert gated == {"nl-toelatingsbesluit"}, "a disarmed homonym is reported, not dropped"
    assert authorisation.passed
    assert "nl-toelatingsbesluit" in matched(authorisation)


def test_the_other_toelating_aliases_are_not_gated(nl_filter: KeywordFilter) -> None:
    for alias in ("toelatingshouder", "toelatingsaanvraag", "herbeoordeling werkzame stof"):
        result = nl_filter.evaluate(Doc(full_text=f"{BOILERPLATE} Het betreft de {alias}."))
        assert result.passed, f"{alias} was never implicated and must keep qualifying alone"


def test_weight_one_terms_never_qualify_alone(nl_filter: KeywordFilter) -> None:
    for contextual in ("lelieteelt", "omwonenden", "residu"):
        result = nl_filter.evaluate(Doc(full_text=f"{BOILERPLATE} Het betreft {contextual}."))
        assert not result.passed, f"{contextual} must not qualify a document on its own"


def test_weight_three_terms_qualify_alone(nl_filter: KeywordFilter) -> None:
    for unambiguous in ("glyfosaat", "spuitzone", "Verordening (EG) nr. 1107/2009"):
        result = nl_filter.evaluate(Doc(full_text=f"{BOILERPLATE} Het betreft {unambiguous}."))
        assert result.passed, f"{unambiguous} must qualify a document on its own"


def test_reach_alone_does_not_qualify_an_eu_document() -> None:
    eu_filter = KeywordFilter(load_keyword_list(KEYWORDS_DIR / "eu.json"))

    contextual = eu_filter.evaluate(Doc(jurisdiction_code="EU", full_text="A REACH dossier."))
    unambiguous = eu_filter.evaluate(
        Doc(jurisdiction_code="EU", full_text="Regulation (EC) No 1107/2009 applies.")
    )

    assert not contextual.passed
    assert unambiguous.passed


def test_every_match_carries_the_provenance_the_keyword_match_table_needs(
    nl_filter: KeywordFilter,
) -> None:
    result = nl_filter.evaluate(
        Doc(title="Glyfosaat en de spuitzone", full_text=f"{BOILERPLATE} spuitzone")
    )

    assert result.matches
    for match in result.matches:
        assert match.term_id in nl_filter.keyword_list.terms
        assert match.list_version == nl_filter.keyword_list.list_version
        assert match.field in nl_filter.keyword_list.field_multipliers
        assert match.weight_applied >= 0
        assert match.snippet
        assert match.occurrences >= 1
    assert result.score == pytest.approx(sum(match.weight_applied for match in result.matches))


# ----------------------------------------------------------------------------------------
# Match modes
# ----------------------------------------------------------------------------------------


def test_word_mode_respects_word_boundaries(tmp_path: Path) -> None:
    stage = build_filter(tmp_path, make_list([term("nl-drift", "drift", 3, match="word")]))

    assert stage.evaluate(Doc(full_text="Sprake van drift, aldus de rechtbank.")).passed
    assert stage.evaluate(Doc(full_text="Er was sprake van spuitdrift.")).passed is False
    assert stage.evaluate(Doc(full_text="De driftreductie was onvoldoende.")).passed is False


def test_phrase_mode_normalises_whitespace(tmp_path: Path) -> None:
    stage = build_filter(tmp_path, make_list([term("nl-stof", "werkzame stof", 3, match="phrase")]))

    assert stage.evaluate(Doc(full_text="de werkzame stof glyfosaat")).passed
    assert stage.evaluate(Doc(full_text="de werkzame\n   stof glyfosaat")).passed
    assert stage.evaluate(Doc(full_text="de werkzamestof")).passed is False


def test_substring_mode_matches_inside_a_compound(tmp_path: Path) -> None:
    stage = build_filter(
        tmp_path,
        make_list([term("nl-middel", "gewasbeschermingsmiddel", 3, match="substring")]),
    )

    result = stage.evaluate(Doc(full_text="de gewasbeschermingsmiddelenrichtlijn"))

    assert result.passed, "a compounding language needs substring matching"
    assert result.matches[0].matched_text == "gewasbeschermingsmiddel"


def test_regex_mode_matches_what_the_other_modes_cannot(tmp_path: Path) -> None:
    stage = build_filter(
        tmp_path,
        make_list([term("nl-artikel", r"artikel\s+5[0-9] van de verordening", 3, match="regex")]),
    )

    matching = stage.evaluate(Doc(full_text="op grond van artikel 53 van de verordening"))
    other = stage.evaluate(Doc(full_text="op grond van artikel 4 van de verordening"))

    assert matching.passed
    assert not other.passed


def test_aliases_score_as_their_parent_and_report_its_id(tmp_path: Path) -> None:
    document = make_list(
        [term("nl-parent", "bestrijdingsmiddel", 3, aliases=["bestrijdingsmiddelen"])]
    )
    stage = build_filter(tmp_path, document)

    result = stage.evaluate(Doc(full_text="het gebruik van bestrijdingsmiddelen"))

    assert result.passed
    assert [match.term_id for match in result.matches] == ["nl-parent"]
    assert result.matches[0].matched_text == "bestrijdingsmiddelen"
    assert result.score == 3.0


# ----------------------------------------------------------------------------------------
# Gates, exclusions, multipliers and counting
# ----------------------------------------------------------------------------------------


def test_requires_gates_a_term_to_zero_until_its_companion_matches(tmp_path: Path) -> None:
    document = make_list(
        [
            term("nl-spray", "bespuiting", 2, match="substring"),
            term("nl-drift", "drift", 3, match="word", requires=["nl-spray"]),
        ]
    )
    stage = build_filter(tmp_path, document)

    alone = stage.evaluate(Doc(full_text="in een vlaag van drift"))
    together = stage.evaluate(Doc(full_text="drift bij de bespuiting"))

    assert alone.score == 0.0
    assert not alone.passed
    assert alone.matches[0].gated
    assert alone.matches[0].weight_applied == 0.0
    assert together.score == 5.0
    assert together.passed


def test_a_gate_opens_only_on_the_named_term(tmp_path: Path) -> None:
    document = make_list(
        [
            term("nl-spray", "bespuiting", 2, match="substring"),
            term("nl-other", "perceel", 1, match="substring"),
            term("nl-drift", "drift", 3, match="word", requires=["nl-spray"]),
        ]
    )
    stage = build_filter(tmp_path, document)

    result = stage.evaluate(Doc(full_text="drift op het perceel"))

    assert result.score == 1.0
    assert not result.passed


def test_an_exclusion_vetoes_regardless_of_score(tmp_path: Path) -> None:
    document = make_list(
        [term("nl-strong", "glyfosaat", 3, match="substring")],
        exclusions=[
            {"pattern": "in een opwelling van drift", "match": "phrase", "reason": "idiom"}
        ],
    )
    stage = build_filter(tmp_path, document)

    result = stage.evaluate(
        Doc(full_text="glyfosaat, glyfosaat, en toch in een opwelling  van drift")
    )

    assert not result.passed
    assert result.score == 0.0
    assert result.matches == ()
    assert "idiom" in result.reason


def test_field_multipliers_apply_where_the_term_matched(tmp_path: Path) -> None:
    document = make_list([term("nl-x", "spuitzone", 2, match="substring")])
    stage = build_filter(tmp_path, document)

    in_title = stage.evaluate(Doc(title="spuitzone", full_text=BOILERPLATE))
    in_body = stage.evaluate(Doc(full_text=f"{BOILERPLATE} spuitzone"))

    assert in_title.score == 4.0, "title multiplier is 2.0 in this list"
    assert in_body.score == 2.0
    assert in_title.passed
    assert not in_body.passed


def test_a_field_the_list_does_not_score_is_not_scanned(tmp_path: Path) -> None:
    document = make_list([term("nl-x", "glyfosaat", 3, match="substring")])
    stage = build_filter(tmp_path, document)

    result = stage.evaluate(Doc(subject="glyfosaat", full_text=BOILERPLATE))

    assert not result.passed
    assert result.matches == ()


def test_count_term_once_credits_the_strongest_field(tmp_path: Path) -> None:
    document = make_list([term("nl-x", "spuitzone", 1, match="substring")])
    stage = build_filter(tmp_path, document)

    result = stage.evaluate(Doc(title="spuitzone", full_text="spuitzone spuitzone spuitzone"))

    by_field = {match.field: match for match in result.matches}
    assert result.score == 2.0, "counted once, credited to the title multiplier"
    assert by_field["title"].weight_applied == 2.0
    assert by_field["full_text"].weight_applied == 0.0
    assert by_field["full_text"].occurrences == 3, "occurrences stay visible for tuning"


def test_without_count_term_once_every_occurrence_counts(tmp_path: Path) -> None:
    document = make_list(
        [term("nl-x", "spuitzone", 1, match="substring")],
        scoring={
            "min_score": 3,
            "count_term_once": False,
            "fields": {"title": 2.0, "abstract": 1.5, "full_text": 1.0},
        },
    )
    stage = build_filter(tmp_path, document)

    result = stage.evaluate(Doc(full_text="spuitzone, spuitzone en nog eens spuitzone"))

    assert result.score == 3.0
    assert result.passed
    assert result.matches[0].occurrences == 3


def test_terms_written_alike_in_several_languages_are_all_credited() -> None:
    eu_filter = KeywordFilter(load_keyword_list(KEYWORDS_DIR / "eu.json"))

    result = eu_filter.evaluate(Doc(jurisdiction_code="EU", full_text="the word pesticide here"))

    assert {"en-pesticide", "fr-pesticide", "nl-eu-pesticide"} <= matched(result)


# ----------------------------------------------------------------------------------------
# Diacritics and casing
# ----------------------------------------------------------------------------------------


def test_folding_preserves_length_so_offsets_stay_valid() -> None:
    text = "neonicotinoïde, Rückstandshöchstgehalt, résidu, ß"

    assert len(fold_diacritics(text)) == len(text)
    assert len(fold_diacritics(text, lower=True)) == len(text)
    assert "neonicotinoide" in fold_diacritics(text)
    assert "ruckstandshochstgehalt" in fold_diacritics(text, lower=True)
    assert "ß" in fold_diacritics(text), "a character with no single-character base is kept"


def test_an_undiacritised_term_finds_a_diacritised_document(nl_filter: KeywordFilter) -> None:
    result = nl_filter.evaluate(Doc(full_text=f"{BOILERPLATE} Het middel bevat neonicotinoïden."))

    assert result.passed
    assert matched(result) == {"nl-neonicotinoide"}
    assert result.matches[0].matched_text == "neonicotinoïden", (
        "the reported evidence must be the document's own spelling"
    )


def test_a_diacritised_term_finds_an_undiacritised_document(tmp_path: Path) -> None:
    stage = build_filter(
        tmp_path, make_list([term("nl-x", "neonicotinoïde", 3, match="substring")])
    )

    assert stage.evaluate(Doc(full_text="het middel bevat neonicotinoide")).passed


def test_a_decomposed_document_is_matched_and_reported_readably(
    nl_filter: KeywordFilter,
) -> None:
    """A source that writes ``ï`` as ``i`` plus a combining mark must still match."""
    decomposed = unicodedata.normalize("NFD", "Het middel bevat neonicotinoïden.")
    assert not unicodedata.is_normalized("NFC", decomposed)

    result = nl_filter.evaluate(Doc(full_text=decomposed))

    assert result.passed
    assert result.matches[0].matched_text == "neonicotinoïden"


def test_offsets_point_into_the_document_text(nl_filter: KeywordFilter) -> None:
    text = f"{BOILERPLATE} Het middel bevat neonicotinoïden en glyfosaat."

    result = nl_filter.evaluate(Doc(full_text=text))

    for match in result.matches:
        assert text[match.start : match.end] == match.matched_text


def test_case_sensitive_terms_keep_their_casing(nl_filter: KeywordFilter) -> None:
    upper = nl_filter.evaluate(Doc(full_text=f"{BOILERPLATE} Het rapport noemt DDT."))
    lower = nl_filter.evaluate(Doc(full_text=f"{BOILERPLATE} Het rapport noemt ddt."))

    assert upper.passed
    assert not lower.passed, "a case-sensitive acronym must not match its lowercase homonym"


def test_case_sensitive_terms_are_not_broken_by_folding(nl_filter: KeywordFilter) -> None:
    exact = nl_filter.evaluate(Doc(full_text=f"{BOILERPLATE} Het Ctgb heeft besloten."))
    shouted = nl_filter.evaluate(Doc(full_text=f"{BOILERPLATE} Het CTGB heeft besloten."))

    assert exact.passed
    assert not shouted.passed


def test_case_insensitive_terms_match_any_casing(nl_filter: KeywordFilter) -> None:
    for spelling in ("glyfosaat", "Glyfosaat", "GLYFOSAAT"):
        assert nl_filter.evaluate(Doc(full_text=f"{BOILERPLATE} {spelling}")).passed


# ----------------------------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------------------------


def test_the_snippet_shows_the_term_in_context(nl_filter: KeywordFilter) -> None:
    text = f"{BOILERPLATE * 3} De bespuiting vond plaats op het perceel. {BOILERPLATE * 3}"

    result = nl_filter.evaluate(Doc(full_text=text))
    snippet = next(match.snippet for match in result.matches if match.term_id == "nl-bespuiting")

    assert "bespuiting" in snippet
    assert snippet.startswith("…")
    assert snippet.endswith("…")
    assert len(snippet) < 250, "a snippet is context, not the document"


def test_the_reason_explains_the_verdict(nl_list: KeywordList, nl_filter: KeywordFilter) -> None:
    passed = nl_filter.evaluate(Doc(full_text="glyfosaat"))
    failed = nl_filter.evaluate(Doc(full_text="lelieteelt"))
    nothing = nl_filter.evaluate(Doc(full_text="een gewoon huurgeschil"))

    assert "reaches" in passed.reason
    assert "nl-glyfosaat" in passed.reason
    assert "below" in failed.reason
    # Taken from the loaded list: curation bumps list_version, and that must not break a
    # test of the matcher's reporting.
    assert f"NL list v{nl_list.list_version}" in failed.reason
    assert "no curated term" in nothing.reason
    assert nothing.matches == ()


def test_the_reason_is_abbreviated_when_many_terms_contribute(tmp_path: Path) -> None:
    document = make_list([term(f"nl-t{index}", f"kwestie{index}", 1) for index in range(12)])
    stage = build_filter(tmp_path, document)

    result = stage.evaluate(Doc(full_text=" ".join(f"kwestie{index}" for index in range(12))))

    assert result.passed
    assert "and 4 more" in result.reason
    assert len(result.matches) == 12, "every match is still reported for keyword_match"


def test_an_empty_document_is_rejected_without_error(nl_filter: KeywordFilter) -> None:
    result = nl_filter.evaluate(Doc())

    assert not result.passed
    assert result.score == 0.0
    assert result.stage == "keywords"


def test_a_negative_snippet_radius_is_refused(nl_list: KeywordList) -> None:
    with pytest.raises(ValueError, match="snippet_radius"):
        KeywordFilter(nl_list, snippet_radius=-1)


# ----------------------------------------------------------------------------------------
# Performance
# ----------------------------------------------------------------------------------------


def _one_megabyte() -> str:
    """Build a megabyte of judgment-like text with a few terms buried in it."""
    half = 500_000
    filler = (BOILERPLATE * (half // len(BOILERPLATE) + 1))[:half]
    return f"{filler} De spuitzone en de lelieteelt, met neonicotinoïde. {filler}"[:1_000_000]


def _synthetic_word(index: int) -> str:
    """Build a distinct nonsense term whose first letters vary, so the trie stays wide."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    value = index
    prefix = []
    for _ in range(3):
        prefix.append(letters[value % 26])
        value //= 26
    return f"{''.join(prefix)}kwestie"


def _fastest(stage: KeywordFilter, document: Doc, rounds: int = 3) -> float:
    """Return the fastest of several evaluations, in seconds."""
    best = float("inf")
    for _ in range(rounds):
        started = time.perf_counter()
        stage.evaluate(document)
        best = min(best, time.perf_counter() - started)
    return best


def test_a_megabyte_of_full_text_matches_well_under_a_second(
    nl_filter: KeywordFilter,
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = _one_megabyte()
    document = Doc(full_text=text)

    elapsed = _fastest(nl_filter, document)

    with capsys.disabled():
        print(f"\n1 MB of full text: {elapsed * 1000:.0f} ms")  # noqa: T201 - the measurement
    assert nl_filter.evaluate(document).passed
    assert len(text) == 1_000_000
    assert elapsed < 0.5, f"1 MB took {elapsed * 1000:.0f} ms"


def test_cost_does_not_grow_with_the_number_of_terms(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Twenty times the terms must not cost twenty times the time.

    A trie shares the prefixes of its terms, so the scan stays proportional to the length of
    the text. A flat alternation, or one scan per term, would fail this outright.
    """
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small = build_filter(
        tmp_path / "small",
        make_list([term(f"nl-t{index:04d}", _synthetic_word(index), 1) for index in range(25)]),
    )
    large = build_filter(
        tmp_path / "large",
        make_list([term(f"nl-t{index:04d}", _synthetic_word(index), 1) for index in range(500)]),
    )
    document = Doc(full_text=_one_megabyte())

    small_time = _fastest(small, document)
    large_time = _fastest(large, document)

    with capsys.disabled():
        print(  # noqa: T201 - the measurement is the point of the test
            f"\n1 MB, 25 terms: {small_time * 1000:.1f} ms; 500 terms: {large_time * 1000:.1f} ms"
        )
    assert large.keyword_list.term_count == 20 * small.keyword_list.term_count
    assert large_time < 4 * small_time, (
        f"500 terms cost {large_time / small_time:.1f}x the time of 25"
    )
