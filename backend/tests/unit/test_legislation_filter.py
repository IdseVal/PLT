"""Filter stage 1: the legislation matcher.

The matcher decides what enters the database (``docs/CORE_DOCUMENT.md`` section 2.5), so
these tests exercise the semantics the legislation lists rely on - every ``match`` mode,
``aliases``, the scanned fields and diacritic folding - against the two shipped lists and
against synthetic lists written into ``tmp_path``.

Selection is a search for names: one listed instrument being named is enough, and there is
no gate, no veto and no score. Several tests below guard exactly that, by naming the
citation forms Dutch case-law reporters use and asserting they select nothing.

The shipped lists are content-manager-owned data. Nothing here edits them; the synthetic
lists exist precisely so that no test ever has a reason to.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from plt.pipeline.filters.base import FilterResult
from plt.pipeline.filters.legislation import (
    LegislationFilter,
    LegislationList,
    LegislationListError,
    LegislationListNotFoundError,
    LegislationListValidationError,
    fold_diacritics,
    load_legislation_list,
    load_legislation_list_for,
)
from tests.conftest import REPO_ROOT, build_settings

LEGISLATION_DIR = REPO_ROOT / "data" / "legislation"
SCHEMA_PATH = LEGISLATION_DIR / "schema.json"

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
    **overrides: Any,  # noqa: ANN401 - passes schema fields straight through
) -> dict[str, Any]:
    """Build one instrument entry for a synthetic list."""
    entry: dict[str, Any] = {
        "id": term_id,
        "term": text,
        "lang": "nl",
        "category": "regulation",
    }
    entry.update(overrides)
    return entry


def make_list(terms: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    """Build a minimal schema-valid list document around a set of instruments."""
    document: dict[str, Any] = {
        "schema_version": "1.0.0",
        "jurisdiction": "NL",
        "jurisdiction_name": "Netherlands",
        "list_version": "9.9.9",
        "updated": "2026-09-17",
        "languages": ["nl"],
        "fields": ["title", "abstract", "full_text"],
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


def build_filter(tmp_path: Path, document: dict[str, Any]) -> LegislationFilter:
    """Compile a synthetic list into a filter stage."""
    return LegislationFilter(load_legislation_list(write_list(tmp_path, document)))


def matched(result: FilterResult) -> set[str]:
    """Return the ids of the instruments that selected the document."""
    return {match.term_id for match in result.labels}


# ----------------------------------------------------------------------------------------
# Loading and validation
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["nl", "eu"])
def test_shipped_lists_load_and_validate(code: str) -> None:
    path = LEGISLATION_DIR / f"{code}.json"
    listing = load_legislation_list(path)
    # Derived from the file rather than hard-coded: these lists are curated by the content
    # manager and regenerated from CELLAR, and an act added must not break the matcher's
    # tests.
    term_count = len(json.loads(path.read_text(encoding="utf-8"))["terms"])

    assert listing.jurisdiction == code.upper()
    assert listing.term_count == term_count
    assert set(listing.fields) >= {"title", "abstract", "full_text"}
    assert listing.pattern_count > listing.term_count, "aliases must be compiled too"
    assert listing.categories, "every list must classify its instruments; the labels come from it"


@pytest.mark.parametrize("code", ["nl", "eu"])
def test_shipped_lists_record_their_own_digest(code: str) -> None:
    path = LEGISLATION_DIR / f"{code}.json"

    assert load_legislation_list(path).digest == hashlib.sha256(path.read_bytes()).hexdigest()


def test_shipped_lists_are_reached_through_the_settings() -> None:
    settings = build_settings()
    for code in ("NL", "EU"):
        listing = load_legislation_list_for(code, settings)
        assert listing.jurisdiction == code
        assert listing.source_path == settings.legislation_list_path(code)


def test_a_list_is_compiled_once_per_process() -> None:
    settings = build_settings()

    first = load_legislation_list_for("NL", settings)
    second = load_legislation_list_for("nl", settings)

    assert first is second


def test_a_missing_list_names_the_jurisdiction(tmp_path: Path) -> None:
    settings = build_settings(legislation_dir=tmp_path)

    with pytest.raises(LegislationListNotFoundError, match="'XX'"):
        load_legislation_list_for("XX", settings)


def test_an_unparsable_list_is_rejected(tmp_path: Path) -> None:
    shutil.copy(SCHEMA_PATH, tmp_path / "schema.json")
    (tmp_path / "nl.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(LegislationListValidationError, match="not valid JSON"):
        load_legislation_list(tmp_path / "nl.json")


def test_an_invalid_term_is_named_in_the_error(tmp_path: Path) -> None:
    document = make_list([term("nl-good", "Wgb"), term("nl-bad", "x", category="statute")])

    with pytest.raises(LegislationListValidationError, match="term 'nl-bad'"):
        load_legislation_list(write_list(tmp_path, document))


def test_the_keyword_schema_fields_are_rejected(tmp_path: Path) -> None:
    """A weight, a gate or an exclusion is a claim the method no longer makes."""
    for stale in ({"weight": 3}, {"requires": ["nl-other"]}):
        document = make_list([term("nl-wgb", "Wgb", **stale)])
        with pytest.raises(LegislationListValidationError, match="term 'nl-wgb'"):
            load_legislation_list(write_list(tmp_path, document))

    document = make_list([term("nl-wgb", "Wgb")], exclusions=[{"pattern": "x", "reason": "y"}])
    with pytest.raises(LegislationListValidationError, match="exclusions"):
        load_legislation_list(write_list(tmp_path, document))


def test_the_previous_schema_version_is_refused(tmp_path: Path) -> None:
    document = make_list([term("nl-wgb", "Wgb")], schema_version="2.0.0")

    with pytest.raises(LegislationListValidationError, match="schema_version"):
        load_legislation_list(write_list(tmp_path, document))


def test_duplicate_term_ids_are_rejected(tmp_path: Path) -> None:
    document = make_list([term("nl-dup", "eerste"), term("nl-dup", "tweede")])

    with pytest.raises(LegislationListValidationError, match="duplicate"):
        load_legislation_list(write_list(tmp_path, document))


def test_a_prose_alias_may_not_inherit_case_sensitive(tmp_path: Path) -> None:
    """``case_sensitive`` applies to every alias, so an acronym term carries acronyms only."""
    document = make_list(
        [term("nl-wgb", "WGB", case_sensitive=True, aliases=["gewasbeschermingswet"])]
    )

    with pytest.raises(LegislationListValidationError) as caught:
        load_legislation_list(write_list(tmp_path, document))

    message = str(caught.value)
    assert "term 'nl-wgb'" in message
    assert "'gewasbeschermingswet'" in message
    assert "not an acronym" in message


def test_a_mixed_case_acronym_is_not_admitted_as_a_literal(tmp_path: Path) -> None:
    """A mixed-case acronym cannot be a case-sensitive literal.

    ``Wgb`` carries a lowercase letter, which is why the shipped list matches it as an
    expression rather than a literal.
    """
    document = make_list([term("nl-wgb", "Wgb", case_sensitive=True)])

    with pytest.raises(LegislationListValidationError, match="not an acronym"):
        load_legislation_list(write_list(tmp_path, document))


def test_a_case_sensitive_term_of_acronyms_only_is_what_the_rule_allows(tmp_path: Path) -> None:
    document = make_list([term("nl-bmw", "BMW", case_sensitive=True, aliases=["BMW1962"])])

    listing = load_legislation_list(write_list(tmp_path, document))

    assert listing.terms["nl-bmw"].case_sensitive is True


def test_upper_casing_a_spelled_out_name_is_not_a_way_past_the_rule(tmp_path: Path) -> None:
    document = make_list([term("nl-wgb", "WET GEWASBESCHERMINGSMIDDELEN", case_sensitive=True)])

    with pytest.raises(LegislationListValidationError, match="not an acronym"):
        load_legislation_list(write_list(tmp_path, document))


def test_a_regex_term_is_read_as_an_expression_and_not_as_a_literal(tmp_path: Path) -> None:
    document = make_list(
        [term("nl-wgb", r"(?<!\w)Wgb(?!\w)", label="Wgb", match="regex", case_sensitive=True)]
    )

    stage = build_filter(tmp_path, document)

    assert matched(stage.evaluate(Doc(full_text="op grond van de Wgb"))) == {"nl-wgb"}
    assert matched(stage.evaluate(Doc(full_text="op grond van de wgb"))) == set()
    assert matched(stage.evaluate(Doc(full_text="WGB"))) == set()


def test_a_declared_exception_admits_the_alias_the_rule_would_refuse(tmp_path: Path) -> None:
    document = make_list(
        [
            term(
                "nl-wgb",
                "WGB",
                case_sensitive=True,
                case_sensitive_exception=True,
                aliases=["gewasbeschermingswet"],
            )
        ]
    )

    listing = load_legislation_list(write_list(tmp_path, document))

    assert listing.terms["nl-wgb"].case_sensitive_exception is True


def test_an_exception_on_a_term_that_is_not_case_sensitive_is_rejected(tmp_path: Path) -> None:
    document = make_list([term("nl-wgb", "wgb", case_sensitive_exception=True)])

    with pytest.raises(LegislationListValidationError, match="not case_sensitive"):
        load_legislation_list(write_list(tmp_path, document))


def test_an_exception_nothing_needs_is_rejected_so_the_set_cannot_rot(tmp_path: Path) -> None:
    document = make_list(
        [term("nl-wgb", "WGB", case_sensitive=True, case_sensitive_exception=True)]
    )

    with pytest.raises(
        LegislationListValidationError, match="every literal it carries is an acronym"
    ):
        load_legislation_list(write_list(tmp_path, document))


def test_an_exception_on_a_regex_term_is_rejected_as_well(tmp_path: Path) -> None:
    document = make_list(
        [
            term(
                "nl-wgb",
                r"(?<!\w)Wgb(?!\w)",
                label="Wgb",
                match="regex",
                case_sensitive=True,
                case_sensitive_exception=True,
            )
        ]
    )

    with pytest.raises(LegislationListValidationError, match="regular expression"):
        load_legislation_list(write_list(tmp_path, document))


def test_a_short_alias_may_not_inherit_substring(tmp_path: Path) -> None:
    """``match`` applies to every alias, so a substring term carries no fragment."""
    document = make_list(
        [term("nl-bmw", "Bestrijdingsmiddelenwet", match="substring", aliases=["BMW"])]
    )

    with pytest.raises(LegislationListValidationError) as caught:
        load_legislation_list(write_list(tmp_path, document))

    message = str(caught.value)
    assert "term 'nl-bmw'" in message
    assert "'BMW'" in message
    assert "substring" in message


def test_a_substring_literal_at_the_floor_is_accepted(tmp_path: Path) -> None:
    document = make_list([term("nl-abc", "abcdef", match="substring")])

    listing = load_legislation_list(write_list(tmp_path, document))

    assert listing.terms["nl-abc"].match == "substring"


def test_a_short_literal_is_only_refused_where_substring_would_reach_inside_a_word(
    tmp_path: Path,
) -> None:
    document = make_list(
        [term("nl-wgb", "Wgb", match="word"), term("nl-bgb", "Bgb", match="phrase")]
    )

    listing = load_legislation_list(write_list(tmp_path, document))

    assert set(listing.terms) == {"nl-wgb", "nl-bgb"}


def test_an_uncompilable_regex_term_is_named(tmp_path: Path) -> None:
    document = make_list([term("nl-bad", "(unclosed", label="bad", match="regex")])

    with pytest.raises(LegislationListValidationError, match="term 'nl-bad'"):
        load_legislation_list(write_list(tmp_path, document))


def test_a_missing_schema_is_reported(tmp_path: Path) -> None:
    (tmp_path / "nl.json").write_text(json.dumps(make_list([term("nl-wgb", "Wgb")])), "utf-8")

    with pytest.raises(LegislationListError, match="schema not found"):
        load_legislation_list(tmp_path / "nl.json")


def test_a_missing_list_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(LegislationListNotFoundError):
        load_legislation_list(tmp_path / "absent.json", schema_path=SCHEMA_PATH)


def test_a_violation_outside_the_terms_array_is_located(tmp_path: Path) -> None:
    document = make_list([term("nl-wgb", "Wgb")], languages=[])

    with pytest.raises(LegislationListValidationError, match="at languages"):
        load_legislation_list(write_list(tmp_path, document))


def test_a_blank_alias_is_rejected(tmp_path: Path) -> None:
    document = make_list([term("nl-wgb", "Wgb", aliases=["   "])])

    with pytest.raises(LegislationListValidationError, match="empty once stripped"):
        load_legislation_list(write_list(tmp_path, document))


def test_a_stage_is_built_for_a_jurisdiction_through_the_settings() -> None:
    stage = LegislationFilter.for_jurisdiction("nl", settings=build_settings())

    assert stage.legislation_list.jurisdiction == "NL"
    assert stage.name == "legislation"


def test_provenance_fields_are_carried_but_never_matched(tmp_path: Path) -> None:
    document = make_list(
        [
            term(
                "nl-wgb",
                "Wet gewasbeschermingsmiddelen en biociden",
                match="phrase",
                bwb="BWBR0021670",
                title="Wet van 17 februari 2007, houdende regels omtrent gewasbeschermingsmiddelen",
            ),
            term("nl-1107", "1107/2009", match="phrase", celex="32009R1107", derived_from="x"),
        ]
    )

    stage = build_filter(tmp_path, document)
    listing = stage.legislation_list

    assert listing.terms["nl-wgb"].bwb == "BWBR0021670"
    assert listing.terms["nl-1107"].celex == "32009R1107"
    assert matched(stage.evaluate(Doc(full_text="zie BWBR0021670 en 32009R1107"))) == set()


# ----------------------------------------------------------------------------------------
# The shipped lists: what they select and what they leave alone
# ----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def nl_stage() -> LegislationFilter:
    return LegislationFilter.for_jurisdiction("NL", settings=build_settings())


@pytest.fixture(scope="module")
def eu_stage() -> LegislationFilter:
    return LegislationFilter.for_jurisdiction("EU", settings=build_settings())


def test_the_dutch_statute_selects_alone(nl_stage: LegislationFilter) -> None:
    result = nl_stage.evaluate(
        Doc(
            full_text=BOILERPLATE
            + "Het besluit berust op de Wet gewasbeschermingsmiddelen en biociden."
        )
    )

    assert result.passed
    assert matched(result) == {"nl-wgb"}


def test_the_acronym_selects_in_its_own_casing_only(nl_stage: LegislationFilter) -> None:
    assert matched(nl_stage.evaluate(Doc(full_text="artikel 20 van de Wgb"))) == {"nl-wgb-acronym"}
    assert matched(nl_stage.evaluate(Doc(full_text="de wgb"))) == set()
    assert matched(nl_stage.evaluate(Doc(full_text="de WGB"))) == set()


def test_the_union_regulation_selects_a_dutch_case_by_its_number(
    nl_stage: LegislationFilter,
) -> None:
    result = nl_stage.evaluate(
        Doc(full_text=BOILERPLATE + "ingevolge artikel 28 van Verordening 1107/2009")
    )

    assert matched(result) == {"nl-reg-1107-2009"}


def test_an_implementing_act_selects_and_is_its_own_label(nl_stage: LegislationFilter) -> None:
    result = nl_stage.evaluate(
        Doc(full_text=BOILERPLATE + "Uitvoeringsverordening (EU) 2017/2324 van de Commissie")
    )

    assert matched(result) == {"nl-32017r2324"}
    (label,) = result.labels
    assert label.term == "Uitvoeringsverordening (EU) 2017/2324"
    assert label.category == "implementing_regulation"


def test_a_case_law_reporter_citation_does_not_select(nl_stage: LegislationFilter) -> None:
    """A year/number reporter citation is a judgment, not an act.

    ``NJ 2009/128`` is a judgment, not the Sustainable Use Directive, and ``AB 2015/408``
    is a judgment, not the list of candidates for substitution.
    """
    result = nl_stage.evaluate(
        Doc(full_text=BOILERPLATE + "vgl. HR 12 maart 2009, NJ 2009/128 en ABRvS, AB 2015/408")
    )

    assert not result.passed
    assert matched(result) == set()


def test_a_longer_number_does_not_select_on_a_shorter_instrument(
    nl_stage: LegislationFilter,
) -> None:
    result = nl_stage.evaluate(Doc(full_text=BOILERPLATE + "zaaknummer 11107/2009 en 1107/20091"))

    assert matched(result) == set()


def test_a_dutch_case_may_cite_a_union_act_in_english(nl_stage: LegislationFilter) -> None:
    result = nl_stage.evaluate(
        Doc(
            full_text=BOILERPLATE
            + "see Regulation (EC) No 396/2005 on maximum residue levels of pesticides"
        )
    )

    assert matched(result) == {"nl-reg-396-2005"}


def test_the_eu_list_reads_the_number_in_any_language(eu_stage: LegislationFilter) -> None:
    for text in (
        "Regulation (EC) No 1107/2009",
        "règlement (CE) no 1107/2009",
        "Verordnung (EG) Nr. 1107/2009",
        "rozporządzenie (WE) nr 1107/2009",
        "\u03ba\u03b1\u03bd\u03bf\u03bd\u03b9\u03c3\u03bc\u03cc\u03c2 (\u0395\u039a) \u03b1\u03c1\u03b9\u03b8. 1107/2009",  # noqa: E501
    ):
        assert matched(eu_stage.evaluate(Doc(jurisdiction_code="EU", full_text=text))) == {
            "en-reg-1107-2009"
        }, text


def test_the_eu_list_reads_the_bracketed_number_of_a_recent_act(
    eu_stage: LegislationFilter,
) -> None:
    for text in (
        "(EU) 2017/2324",
        "(UE) 2017/2324",
        "(\u0395\u0395) 2017/2324",
        "Verordnung 2017/2324",
    ):
        assert matched(eu_stage.evaluate(Doc(jurisdiction_code="EU", full_text=text))) == {
            "en-32017r2324"
        }, text


def test_a_predecessor_directive_still_selects(eu_stage: LegislationFilter) -> None:
    result = eu_stage.evaluate(
        Doc(jurisdiction_code="EU", full_text="Annex I to Directive 91/414/EEC")
    )

    assert matched(result) == {"en-dir-91-414"}


def test_the_general_chemicals_regime_does_not_select(eu_stage: LegislationFilter) -> None:
    """REACH, CLP and the Water Framework Directive were considered and left off."""
    result = eu_stage.evaluate(
        Doc(
            jurisdiction_code="EU",
            full_text=(
                "Regulation (EC) No 1907/2006 (REACH), Regulation (EC) No 1272/2008 "
                "and Directive 2000/60/EC"
            ),
        )
    )

    assert not result.passed


def test_every_shipped_instrument_has_a_readable_label() -> None:
    for code in ("NL", "EU"):
        listing = load_legislation_list_for(code, build_settings())
        for instrument in listing.terms.values():
            label = instrument.public_label
            assert "(?" not in label and "\\" not in label, (
                f"{instrument.term_id} publishes a pattern"
            )
            assert label.strip() == label and len(label) >= 3


def test_every_generated_entry_names_its_base_instrument_and_celex() -> None:
    for code in ("nl", "eu"):
        document = json.loads((LEGISLATION_DIR / f"{code}.json").read_text(encoding="utf-8"))
        for entry in document["terms"]:
            if "derived_from" in entry:
                assert entry.get("celex"), f"{entry['id']} was generated without a CELEX number"
                assert entry.get("aliases"), f"{entry['id']} was generated without a citation form"


def test_every_match_carries_the_provenance_the_keyword_match_table_needs(
    nl_stage: LegislationFilter,
) -> None:
    result = nl_stage.evaluate(
        Doc(title="Wet gewasbeschermingsmiddelen en biociden", full_text=BOILERPLATE)
    )

    (match,) = result.labels
    assert match.term_id == "nl-wgb"
    assert match.term == "Wet gewasbeschermingsmiddelen en biociden"
    assert match.category == "national_act"
    assert match.list_version == nl_stage.legislation_list.list_version
    assert match.field == "title"
    assert match.occurrences == 1
    assert match.matched_text == "Wet gewasbeschermingsmiddelen en biociden"


# ----------------------------------------------------------------------------------------
# Match modes and aliases
# ----------------------------------------------------------------------------------------


def test_word_mode_respects_word_boundaries(tmp_path: Path) -> None:
    stage = build_filter(tmp_path, make_list([term("nl-wet", "wet")]))

    assert matched(stage.evaluate(Doc(full_text="de wet"))) == {"nl-wet"}
    assert matched(stage.evaluate(Doc(full_text="de wetgeving"))) == set()


def test_phrase_mode_normalises_whitespace(tmp_path: Path) -> None:
    stage = build_filter(
        tmp_path,
        make_list([term("nl-rtb", "Regeling toelating bestrijdingsmiddelen", match="phrase")]),
    )

    assert matched(
        stage.evaluate(Doc(full_text="de Regeling   toelating\nbestrijdingsmiddelen"))
    ) == {"nl-rtb"}


def test_a_bracketed_number_needs_no_boundary_before_the_bracket(tmp_path: Path) -> None:
    stage = build_filter(tmp_path, make_list([term("nl-act", "(EU) 2017/2324", match="phrase")]))

    assert matched(stage.evaluate(Doc(full_text="Uitvoeringsverordening (EU) 2017/2324 van"))) == {
        "nl-act"
    }
    assert matched(stage.evaluate(Doc(full_text="(EU) 2017/23245"))) == set()


def test_substring_mode_matches_inside_a_compound(tmp_path: Path) -> None:
    stage = build_filter(
        tmp_path, make_list([term("nl-bmw", "Bestrijdingsmiddelenwet", match="substring")])
    )

    assert matched(stage.evaluate(Doc(full_text="de Bestrijdingsmiddelenwetgeving"))) == {"nl-bmw"}


def test_regex_mode_matches_what_the_other_modes_cannot(tmp_path: Path) -> None:
    stage = build_filter(
        tmp_path,
        make_list([term("nl-art", r"artikel\s+\d+\s+Wgb", label="artikel Wgb", match="regex")]),
    )

    result = stage.evaluate(Doc(full_text="ingevolge artikel  28 Wgb"))

    assert matched(result) == {"nl-art"}
    (match,) = result.labels
    assert match.term == "artikel Wgb"


def test_aliases_report_their_parent_and_its_label(tmp_path: Path) -> None:
    stage = build_filter(
        tmp_path,
        make_list(
            [
                term(
                    "nl-wgb",
                    "Wet gewasbeschermingsmiddelen en biociden",
                    match="phrase",
                    aliases=["Gewasbeschermingswet"],
                )
            ]
        ),
    )

    result = stage.evaluate(Doc(full_text="de Gewasbeschermingswet"))

    assert matched(result) == {"nl-wgb"}
    (match,) = result.labels
    assert match.term == "Wet gewasbeschermingsmiddelen en biociden"
    assert match.matched_text == "Gewasbeschermingswet"


# ----------------------------------------------------------------------------------------
# Fields and labels
# ----------------------------------------------------------------------------------------


def test_a_match_is_reported_against_the_field_it_was_found_in(tmp_path: Path) -> None:
    stage = build_filter(tmp_path, make_list([term("nl-wgb", "Wgb")]))

    result = stage.evaluate(Doc(abstract="de Wgb", full_text=BOILERPLATE))

    assert [match.field for match in result.matches] == ["abstract"]


def test_a_field_the_list_does_not_name_is_not_scanned(tmp_path: Path) -> None:
    stage = build_filter(tmp_path, make_list([term("nl-wgb", "Wgb")], fields=["title"]))

    assert matched(stage.evaluate(Doc(full_text="de Wgb"))) == set()
    assert matched(stage.evaluate(Doc(title="de Wgb"))) == {"nl-wgb"}


def test_an_instrument_found_in_two_fields_is_still_one_label(tmp_path: Path) -> None:
    stage = build_filter(tmp_path, make_list([term("nl-wgb", "Wgb")]))

    result = stage.evaluate(Doc(title="Wgb", full_text="de Wgb en nogmaals de Wgb"))

    assert len(result.matches) == 2
    assert len(result.labels) == 1
    assert result.matched_term_count == 1
    assert result.labels[0].field == "title"


def test_a_citation_form_shared_by_two_instruments_labels_both(tmp_path: Path) -> None:
    stage = build_filter(
        tmp_path,
        make_list(
            [
                term("nl-one", "540/2011", match="phrase"),
                term(
                    "nl-two",
                    "Uitvoeringsverordening (EU) nr. 540/2011",
                    match="phrase",
                    aliases=["540/2011"],
                ),
            ]
        ),
    )

    assert matched(stage.evaluate(Doc(full_text="zie 540/2011"))) == {"nl-one", "nl-two"}


# ----------------------------------------------------------------------------------------
# Folding, offsets and snippets
# ----------------------------------------------------------------------------------------


def test_folding_preserves_length_so_offsets_stay_valid() -> None:
    text = "règlement délégué (UE) 2021/525 ÉÊË ß"

    assert len(fold_diacritics(text)) == len(text)
    assert len(fold_diacritics(text, lower=True)) == len(text)
    assert fold_diacritics("règlement") == "reglement"
    assert fold_diacritics("ß") == "ß"


def test_an_undiacritised_term_finds_a_diacritised_document(tmp_path: Path) -> None:
    stage = build_filter(
        tmp_path, make_list([term("fr-reg", "reglement delegue", match="phrase", lang="fr")])
    )

    result = stage.evaluate(Doc(full_text="le règlement délégué"))

    assert matched(result) == {"fr-reg"}
    assert result.labels[0].matched_text == "règlement délégué"


def test_a_diacritised_term_finds_an_undiacritised_document(tmp_path: Path) -> None:
    stage = build_filter(
        tmp_path, make_list([term("fr-reg", "règlement délégué", match="phrase", lang="fr")])
    )

    assert matched(stage.evaluate(Doc(full_text="le reglement delegue"))) == {"fr-reg"}


def test_a_decomposed_document_is_matched_and_reported_readably(tmp_path: Path) -> None:
    stage = build_filter(tmp_path, make_list([term("fr-reg", "règlement", lang="fr")]))
    decomposed = unicodedata.normalize("NFD", "le règlement")

    result = stage.evaluate(Doc(full_text=decomposed))

    assert matched(result) == {"fr-reg"}
    assert result.labels[0].matched_text == "règlement"


def test_offsets_point_into_the_document_text(tmp_path: Path) -> None:
    stage = build_filter(tmp_path, make_list([term("nl-wgb", "Wgb")]))
    text = "artikel 20 van de Wgb bepaalt"

    (match,) = stage.evaluate(Doc(full_text=text)).labels

    assert text[match.start : match.end] == "Wgb"


def test_case_sensitive_terms_keep_their_casing(tmp_path: Path) -> None:
    stage = build_filter(tmp_path, make_list([term("nl-bmw", "BMW", case_sensitive=True)]))

    assert matched(stage.evaluate(Doc(full_text="de BMW"))) == {"nl-bmw"}
    assert matched(stage.evaluate(Doc(full_text="de bmw"))) == set()


def test_case_insensitive_terms_match_any_casing(tmp_path: Path) -> None:
    stage = build_filter(tmp_path, make_list([term("nl-wgb", "wgb")]))

    for text in ("wgb", "Wgb", "WGB"):
        assert matched(stage.evaluate(Doc(full_text=text))) == {"nl-wgb"}, text


def test_the_snippet_shows_the_instrument_in_context(tmp_path: Path) -> None:
    stage = LegislationFilter(
        load_legislation_list(write_list(tmp_path, make_list([term("nl-wgb", "Wgb")]))),
        snippet_radius=8,
    )

    (match,) = stage.evaluate(Doc(full_text="a" * 40 + " zie de Wgb voor " + "b" * 40)).labels

    assert match.snippet.startswith("…")
    assert match.snippet.endswith("…")
    assert "Wgb" in match.snippet
    assert len(match.snippet) < 30


def test_the_reason_explains_the_verdict(tmp_path: Path) -> None:
    stage = build_filter(tmp_path, make_list([term("nl-wgb", "Wgb"), term("nl-bgb", "Bgb")]))

    passed = stage.evaluate(Doc(full_text="Wgb en Bgb"))
    failed = stage.evaluate(Doc(full_text=BOILERPLATE))

    assert passed.reason == "named 2 listed instrument(s) (NL list v9.9.9): nl-wgb, nl-bgb"
    assert failed.reason == "no listed instrument named (NL list v9.9.9)"
    assert not failed.passed
    assert failed.stage == "legislation"


def test_the_reason_is_abbreviated_when_many_instruments_contribute(tmp_path: Path) -> None:
    terms = [term(f"nl-t{index}", f"instrument{index}") for index in range(12)]
    stage = build_filter(tmp_path, make_list(terms))

    result = stage.evaluate(Doc(full_text=" ".join(f"instrument{index}" for index in range(12))))

    assert result.reason.endswith("and 4 more")


def test_an_empty_document_is_rejected_without_error(tmp_path: Path) -> None:
    stage = build_filter(tmp_path, make_list([term("nl-wgb", "Wgb")]))

    result = stage.evaluate(Doc())

    assert not result.passed
    assert result.matches == ()


def test_a_negative_snippet_radius_is_refused(tmp_path: Path) -> None:
    listing = load_legislation_list(write_list(tmp_path, make_list([term("nl-wgb", "Wgb")])))

    with pytest.raises(ValueError, match="snippet_radius"):
        LegislationFilter(listing, snippet_radius=-1)


# ----------------------------------------------------------------------------------------
# Cost
# ----------------------------------------------------------------------------------------


def _megabyte_of_judgment() -> str:
    """Return a megabyte of Dutch legal prose with a handful of citations in it."""
    citation = " Het besluit berust op artikel 28 van Verordening (EG) nr. 1107/2009. "
    chunk = BOILERPLATE * 20 + citation
    return (chunk * (1_000_000 // len(chunk) + 1))[:1_000_000]


def test_a_megabyte_of_full_text_matches_well_under_a_second(nl_stage: LegislationFilter) -> None:
    text = _megabyte_of_judgment()
    nl_stage.evaluate(Doc(full_text=text))  # warm up

    started = time.perf_counter()
    result = nl_stage.evaluate(Doc(full_text=text))
    elapsed = time.perf_counter() - started

    assert result.passed
    instruments = nl_stage.legislation_list.term_count
    print(f"\n1 MB of full text against {instruments} instruments: {elapsed * 1000:.0f} ms")  # noqa: T201
    assert elapsed < 0.5, f"1 MB took {elapsed:.2f}s; the shipped list must stay fast"


def test_cost_does_not_grow_with_the_number_of_instruments(tmp_path: Path) -> None:
    text = _megabyte_of_judgment()
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small = build_filter(
        tmp_path / "small", make_list([term("nl-1107", "1107/2009", match="phrase")])
    )
    large = build_filter(
        tmp_path / "large",
        make_list(
            [term("nl-1107", "1107/2009", match="phrase")]
            + [
                term(f"nl-act{index}", f"(EU) {2015 + index % 12}/{index + 100}", match="phrase")
                for index in range(3000)
            ]
        ),
    )
    for stage in (small, large):
        stage.evaluate(Doc(full_text=text))

    started = time.perf_counter()
    small.evaluate(Doc(full_text=text))
    small_elapsed = time.perf_counter() - started
    started = time.perf_counter()
    large.evaluate(Doc(full_text=text))
    large_elapsed = time.perf_counter() - started

    assert large.legislation_list.term_count == 3001
    assert large_elapsed < max(0.5, small_elapsed * 4), (small_elapsed, large_elapsed)


def test_a_regex_term_must_say_what_to_call_it(tmp_path: Path) -> None:
    document = make_list([term("nl-wgb", r"(?<!\w)Wgb(?!\w)", match="regex")])

    with pytest.raises(LegislationListValidationError, match="label"):
        load_legislation_list(write_list(tmp_path, document))


def test_list_metadata_is_exposed(tmp_path: Path) -> None:
    listing: LegislationList = load_legislation_list(
        write_list(
            tmp_path,
            make_list(
                [
                    term("nl-wgb", "Wgb", category="national_act"),
                    term("nl-r", "1107/2009", match="phrase"),
                ]
            ),
        )
    )

    assert listing.list_version == "9.9.9"
    assert listing.updated == "2026-09-17"
    assert listing.languages == ("nl",)
    assert listing.categories == ("national_act", "regulation")
    assert listing.scan_count >= 1
