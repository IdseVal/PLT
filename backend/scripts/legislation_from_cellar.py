"""Enumerate the Union acts that hang off the pesticide base instruments, from CELLAR.

The legislation lists in ``data/legislation/`` carry two kinds of entry. The base
instruments - Regulation (EC) No 1107/2009 and its predecessor, the residue, biocide,
sustainable-use and statistics instruments - are hand-curated: their customary short names
and their citation forms in each language are written by the content manager. Everything
that implements, amends or corrects one of them is enumerated here rather than typed:
CELLAR records those relations (``resource_legal_based_on_resource_legal``,
``resource_legal_amends_resource_legal``, ``resource_legal_corrects_resource_legal``), and a
list of two thousand implementing acts written from memory would be a list with a recall
floor.

Every entry this script writes carries ``derived_from`` naming the base instrument it was
found under, and that key is how the script tells its own entries from the curator's: a run
replaces every entry that has it and touches none that does not. Nothing here bumps
``list_version``; deciding whether a regenerated list changes what is selected is the
curator's call (``data/legislation/README.md``).

What is kept is legislation: CELEX sector 3, of the regulation, directive and decision
types EUR-Lex distinguishes. Proposals, reports, resolutions and corrigenda are related to
the base instruments in the same way and are dropped, because a judgment does not cite a
proposal as the law it applies and a corrigendum has the number of the act it corrects.

Usage::

    python scripts/legislation_from_cellar.py --cache .cache/cellar data/legislation/eu.json
    python scripts/legislation_from_cellar.py --cache .cache/cellar data/legislation/nl.json

The cache directory keeps one CSV per base instrument so a second run reads no network;
delete it, or pass ``--refresh``, to ask CELLAR again. The endpoint is the public SPARQL
service the connector uses, addressed with the project's User-Agent, one query per base
instrument.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import re
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

log = logging.getLogger("legislation_from_cellar")

SPARQL_URL: Final[str] = "https://publications.europa.eu/webapi/rdf/sparql"
USER_AGENT: Final[str] = "PLT/0.2.0 (+https://github.com/IdseVal/PLT; plt@wur.nl)"

#: The base instruments, and the relation that makes an act relevant: an act is listed when
#: it is based on, amends or corrects one of these. 540/2011 is listed on its own account
#: as well as through 1107/2009, because the approval regulations amend the Annex to
#: 540/2011 and are based on 1107/2009 - the two relations reach different acts.
BASE_INSTRUMENTS: Final[tuple[str, ...]] = (
    "31991L0414",  # Directive 91/414/EEC, plant protection products (repealed)
    "32009R1107",  # Regulation (EC) No 1107/2009, plant protection products
    "32011R0540",  # Implementing Regulation (EU) No 540/2011, approved active substances
    "32005R0396",  # Regulation (EC) No 396/2005, maximum residue levels
    "31998L0008",  # Directive 98/8/EC, biocidal products (repealed)
    "32012R0528",  # Regulation (EU) No 528/2012, biocidal products
    "32009L0128",  # Directive 2009/128/EC, sustainable use of pesticides
    "32009R1185",  # Regulation (EC) No 1185/2009, statistics on pesticides
)

#: EUR-Lex resource types that are legislation, mapped onto the list's categories.
CATEGORY_BY_TYPE: Final[Mapping[str, str]] = {
    "REG": "regulation",
    "REG_IMPL": "implementing_regulation",
    "REG_DEL": "delegated_regulation",
    "DIR": "directive",
    "DIR_IMPL": "implementing_directive",
    "DIR_DEL": "delegated_directive",
    "DEC": "decision",
    "DEC_ENTSCHEID": "decision",
    "DEC_IMPL": "implementing_decision",
    "DEC_DEL": "delegated_decision",
}

LANGUAGE_URI: Final[Mapping[str, str]] = {
    "en": "ENG",
    "nl": "NLD",
    "fr": "FRA",
    "de": "DEU",
}

#: How each of the list's languages writes the bracketed institution token of an instrument
#: number, by the family the token belongs to: "Regulation (EC) No 1107/2009" in English is
#: "Verordening (EG) nr. 1107/2009" in Dutch and "règlement (CE) no 1107/2009" in French.
LANGUAGE_TOKENS: Final[Mapping[str, Mapping[str, str]]] = {
    "en": {"EU": "EU", "EC": "EC", "EEC": "EEC"},
    "nl": {"EU": "EU", "EC": "EG", "EEC": "EEG"},
    "fr": {"EU": "UE", "EC": "CE", "EEC": "CEE"},
    "de": {"EU": "EU", "EC": "EG", "EEC": "EWG"},
}

#: The words each language puts before an instrument number, by category. Used for the
#: unbracketed citation ("Verordening 2017/2324") and, in the list's own language, for the
#: public label.
TYPE_WORDS: Final[Mapping[str, Mapping[str, tuple[str, ...]]]] = {
    "regulation": {
        "en": ("Regulation",),
        "nl": ("Verordening",),
        "fr": ("Règlement",),
        "de": ("Verordnung",),
    },
    "implementing_regulation": {
        "en": ("Implementing Regulation", "Regulation"),
        "nl": ("Uitvoeringsverordening", "Verordening"),
        "fr": ("Règlement d'exécution", "Règlement"),
        "de": ("Durchführungsverordnung", "Verordnung"),
    },
    "delegated_regulation": {
        "en": ("Delegated Regulation", "Regulation"),
        "nl": ("Gedelegeerde Verordening", "Verordening"),
        "fr": ("Règlement délégué", "Règlement"),
        "de": ("Delegierte Verordnung", "Verordnung"),
    },
    "directive": {
        "en": ("Directive",),
        "nl": ("Richtlijn",),
        "fr": ("Directive",),
        "de": ("Richtlinie",),
    },
    "implementing_directive": {
        "en": ("Implementing Directive", "Directive"),
        "nl": ("Uitvoeringsrichtlijn", "Richtlijn"),
        "fr": ("Directive d'exécution", "Directive"),
        "de": ("Durchführungsrichtlinie", "Richtlinie"),
    },
    "delegated_directive": {
        "en": ("Delegated Directive", "Directive"),
        "nl": ("Gedelegeerde Richtlijn", "Richtlijn"),
        "fr": ("Directive déléguée", "Directive"),
        "de": ("Delegierte Richtlinie", "Richtlinie"),
    },
    "decision": {
        "en": ("Decision",),
        "nl": ("Besluit", "Beschikking"),
        "fr": ("Décision",),
        "de": ("Beschluss", "Entscheidung"),
    },
    "implementing_decision": {
        "en": ("Implementing Decision", "Decision"),
        "nl": ("Uitvoeringsbesluit", "Besluit"),
        "fr": ("Décision d'exécution", "Décision"),
        "de": ("Durchführungsbeschluss", "Beschluss"),
    },
    "delegated_decision": {
        "en": ("Delegated Decision", "Decision"),
        "nl": ("Gedelegeerd Besluit", "Besluit"),
        "fr": ("Décision déléguée", "Décision"),
        "de": ("Delegierter Beschluss", "Beschluss"),
    },
}

#: The plural of each generic type word, for "Directives 2003/5/EC and 2003/6/EC".
PLURAL_WORDS: Final[Mapping[str, str]] = {
    "Regulation": "Regulations",
    "Directive": "Directives",
    "Decision": "Decisions",
    "Verordening": "Verordeningen",
    "Richtlijn": "Richtlijnen",
    "Besluit": "Besluiten",
    "Beschikking": "Beschikkingen",
    "Règlement": "Règlements",
    "Décision": "Décisions",
    "Verordnung": "Verordnungen",
    "Richtlinie": "Richtlinien",
    "Beschluss": "Beschlüsse",
    "Entscheidung": "Entscheidungen",
}

#: Below this number a bare number/year citation ("8/2011") is a date as often as an act, so
#: it is carried only behind a "No"-style prefix.
_BARE_NUMBER_FLOOR: Final[int] = 100

#: An act related to a base instrument only by amending it, and amending more acts than this,
#: is an omnibus - a comitology or accession adaptation touching a hundred instruments at
#: once - and not pesticide law. Regulation (EC) No 1882/2003 amends 91/414/EEC and 98/8/EC
#: among many others, and a judgment citing it is about whatever it happens to be about.
_OMNIBUS_FLOOR: Final[int] = 5

#: Bumped whenever the query changes shape, so a cached answer to the old one is not reused.
_QUERY_VERSION: Final[str] = "v2"

_NUMBER_PREFIXES: Final[tuple[str, ...]] = ("No", "No.", "nr.", "Nr.", "n°", "nº", "n.")

_CELEX_SHAPE: Final[re.Pattern[str]] = re.compile(r"^3(\d{4})([RLD])(\d{4})$")
_BRACKETED: Final[re.Pattern[str]] = re.compile(
    r"\((?P<family>EU|EC|EEC|Euratom)(?:,\s*Euratom)?\)\s*(?:No\.?\s*)?(?P<a>\d+)/(?P<b>\d+)"
)
_SUFFIXED: Final[re.Pattern[str]] = re.compile(
    r"(?<![\d/])(?P<a>\d{2,4})/(?P<b>\d+)/(?P<family>EU|EC|EEC|Euratom)\b"
)
_NOISE: Final[re.Pattern[str]] = re.compile(
    r"\s*(?:\(?\s*Text with EEA relevance\.?\s*\)?|\(?\s*Voor de EER relevante tekst\.?\s*\)?"
    r"|\(?\s*Texte présentant de l'intérêt pour l'EEE\.?\s*\)?"
    r"|\(?\s*Text von Bedeutung für den EWR\.?\s*\)?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Citation:
    """How an act is numbered, as the Official Journal printed it.

    Attributes:
        family: ``EU``, ``EC`` or ``EEC``: the institution token the number is bracketed or
            suffixed with.
        year_first: Whether the printed order is year/number (every directive and decision,
            and every regulation from 2015) or number/year (regulations before 2015).
        year: The year as printed, ``91`` or ``2009``.
        number: The sequence number as printed, without leading zeros.
        bracketed: Whether the title brackets the family (``(EU) 2017/2324``) rather than
            suffixing it (``91/414/EEC``).
    """

    family: str
    year_first: bool
    year: str
    number: str
    bracketed: bool

    @property
    def printed(self) -> str:
        """Return the digits in printed order, ``2017/2324`` or ``540/2011``."""
        return f"{self.year}/{self.number}" if self.year_first else f"{self.number}/{self.year}"


@dataclass(slots=True)
class Act:
    """One act as CELLAR describes it, plus what was derived from that description."""

    celex: str
    resource_type: str
    date: str
    in_force: str
    titles: dict[str, str]
    amends_count: int = 0
    bases: set[str] = field(default_factory=set)
    relations: set[str] = field(default_factory=set)

    @property
    def omnibus(self) -> bool:
        """Return whether the act is an omnibus amendment rather than pesticide law."""
        return "based_on" not in self.relations and self.amends_count > _OMNIBUS_FLOOR

    @property
    def category(self) -> str | None:
        """Return the list category for this act's EUR-Lex type, or ``None`` if not law."""
        return CATEGORY_BY_TYPE.get(self.resource_type)


def _query(base: str, languages: Sequence[str]) -> str:
    """Build the SPARQL query listing every act related to one base instrument.

    Args:
        base: CELEX number of the base instrument.
        languages: ISO 639-1 codes whose expression titles to fetch.

    Returns:
        The query text.
    """
    optional_titles = "\n".join(
        f"  OPTIONAL {{ ?e_{code} cdm:expression_belongs_to_work ?act . "
        f"?e_{code} cdm:expression_uses_language "
        f"<http://publications.europa.eu/resource/authority/language/{LANGUAGE_URI[code]}> . "
        f"?e_{code} cdm:expression_title ?t_{code} }}"
        for code in languages
    )
    projected = " ".join(f"(SAMPLE(?t_{code}) AS ?title_{code})" for code in languages)
    return (
        "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
        "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
        f"SELECT ?celex ?rel ?date ?type ?force (COUNT(DISTINCT ?amended) AS ?amends_count) "
        f"{projected} WHERE {{\n"
        f'  ?base cdm:resource_legal_id_celex "{base}"^^xsd:string .\n'
        '  { ?act cdm:resource_legal_based_on_resource_legal ?base . BIND("based_on" AS ?rel) }\n'
        '  UNION { ?act cdm:resource_legal_amends_resource_legal ?base . BIND("amends" AS ?rel) }\n'
        "  UNION { ?act cdm:resource_legal_corrects_resource_legal ?base ."
        ' BIND("corrects" AS ?rel) }\n'
        "  ?act cdm:resource_legal_id_celex ?celex .\n"
        "  OPTIONAL { ?act cdm:work_date_document ?date }\n"
        "  OPTIONAL { ?act cdm:work_has_resource-type ?type }\n"
        "  OPTIONAL { ?act cdm:resource_legal_in-force ?force }\n"
        "  OPTIONAL { ?act cdm:resource_legal_amends_resource_legal ?amended }\n"
        f"{optional_titles}\n"
        "} GROUP BY ?celex ?rel ?date ?type ?force ORDER BY ?celex\n"
    )


def fetch_csv(base: str, languages: Sequence[str], cache: Path, *, refresh: bool) -> str:
    """Return the CSV listing for one base instrument, from the cache or from CELLAR.

    Args:
        base: CELEX number of the base instrument.
        languages: Title languages to fetch.
        cache: Directory holding one CSV per base instrument.
        refresh: Ignore the cache and ask CELLAR again.

    Returns:
        The CSV text.
    """
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{base}_{'-'.join(languages)}_{_QUERY_VERSION}.csv"
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8")
    url = f"{SPARQL_URL}?{urllib.parse.urlencode({'query': _query(base, languages)})}"
    request = urllib.request.Request(  # noqa: S310 - fixed https endpoint
        url, headers={"Accept": "text/csv", "User-Agent": USER_AGENT}
    )
    log.info("asking CELLAR for the acts related to %s", base)
    with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
        text: str = response.read().decode("utf-8")
    path.write_text(text, encoding="utf-8")
    time.sleep(1.0)  # One query per base instrument; there is no reason to hurry the endpoint.
    return text


def collect(languages: Sequence[str], cache: Path, *, refresh: bool) -> dict[str, Act]:
    """Read every base instrument's listing and merge them into one act per CELEX number.

    Args:
        languages: Title languages to fetch.
        cache: CSV cache directory.
        refresh: Ignore the cache.

    Returns:
        Acts keyed by CELEX number.
    """
    acts: dict[str, Act] = {}
    for base in BASE_INSTRUMENTS:
        for row in csv.DictReader(io.StringIO(fetch_csv(base, languages, cache, refresh=refresh))):
            celex = row["celex"]
            act = acts.get(celex)
            if act is None:
                act = Act(
                    celex=celex,
                    resource_type=row.get("type", "").rsplit("/", 1)[-1],
                    date=row.get("date", ""),
                    in_force=row.get("force", ""),
                    amends_count=int(row.get("amends_count") or 0),
                    titles={
                        code: _NOISE.sub("", row.get(f"title_{code}", "") or "").strip()
                        for code in languages
                    },
                )
                acts[celex] = act
            act.bases.add(base)
            act.relations.add(row["rel"])
    return acts


def parse_citation(act: Act) -> Citation | None:
    """Work out how the act is numbered, from its English title, checked against its CELEX.

    Args:
        act: The act.

    Returns:
        The citation, or ``None`` when the act's CELEX number is not a plain legislative one.
    """
    shape = _CELEX_SHAPE.match(act.celex)
    if shape is None:
        return None
    celex_year, kind, celex_number = shape.groups()
    number = str(int(celex_number))
    title = act.titles.get("en") or ""
    found = _BRACKETED.search(title)
    bracketed = found is not None
    if found is None:
        found = _SUFFIXED.search(title)
    if found is not None:
        a, b, family = found.group("a"), found.group("b"), found.group("family")
        family = "EU" if family == "Euratom" else family
        if a == celex_year or (len(a) == 2 and celex_year.endswith(a)):
            if b == number:
                return Citation(family, True, a, number, bracketed)
        elif b == celex_year and a == number:
            return Citation(family, False, celex_year, number, bracketed)
        log.warning(
            "%s: title number %s/%s disagrees with the CELEX; using the CELEX", act.celex, a, b
        )
    # Fallback from the CELEX alone: regulations were number/year until 2015, directives and
    # decisions year/number throughout, EU from December 2009 and EC before it.
    year = int(celex_year)
    family = "EU" if year >= 2010 else ("EC" if year >= 1993 else "EEC")
    year_first = kind != "R" or year >= 2015
    printed_year = celex_year if year >= 2000 else celex_year[2:]
    return Citation(family, year_first, printed_year, number, bracketed=year_first and year >= 2015)


def citation_forms(citation: Citation, category: str, languages: Sequence[str]) -> list[str]:
    """Return every spelling of an act's number a judgment in any language may use.

    Args:
        citation: The act's number.
        category: The list category, which decides the type words.
        languages: The list's languages, which decide which type words are carried.

    Returns:
        Distinct citation forms, in a stable order.
    """
    forms: list[str] = []
    digits = citation.printed
    if not citation.year_first:
        # Regulations before 2015 are the only acts numbered number/year, so the bare number
        # names one act. Below the floor it is as often a date.
        bare = int(citation.number) >= _BARE_NUMBER_FLOOR
        if bare:
            forms.append(digits)
        else:
            forms.extend(f"{prefix} {digits}" for prefix in _NUMBER_PREFIXES)
    for code in languages:
        token = LANGUAGE_TOKENS[code][citation.family]
        for word in TYPE_WORDS[category].get(code, ()):
            # "Directive 2003/5" also reaches "Directive 2003/5/EC": the slash after the
            # number is not a word character. A year/number number is never carried without
            # its type word, because directives, decisions and, from 2015, regulations share
            # one numbering space: Decision 2003/35/EC and Directive 2003/35/EC are
            # different acts, and only the word tells them apart.
            forms.append(f"{word} {digits}")
            if word in PLURAL_WORDS:
                forms.append(f"{PLURAL_WORDS[word]} {digits}")
            if citation.year_first:
                forms.append(f"{word} ({token}) {digits}")
    seen: set[str] = set()
    distinct: list[str] = []
    for form in forms:
        key = form.casefold()
        if key not in seen:
            seen.add(key)
            distinct.append(form)
    return distinct


def short_citation(citation: Citation, category: str, language: str) -> str:
    """Return the act's short citation in one language, which is its public label.

    Args:
        citation: The act's number.
        category: The list category.
        language: ISO 639-1 code of the label language.

    Returns:
        For example ``Implementing Regulation (EU) 2017/2324`` or ``Richtlijn 91/414/EEG``.
    """
    word = TYPE_WORDS[category][language][0]
    token = LANGUAGE_TOKENS[language][citation.family]
    if citation.year_first:
        if citation.bracketed:
            return f"{word} ({token}) {citation.printed}"
        return f"{word} {citation.printed}/{token}"
    number_word = {"nl": "nr.", "fr": "n°", "de": "Nr."}.get(language, "No")
    return f"{word} ({token}) {number_word} {citation.printed}"


def generate_entries(
    acts: Mapping[str, Act],
    *,
    list_language: str,
    languages: Sequence[str],
    curated_celex: Iterable[str],
) -> list[dict[str, Any]]:
    """Turn the collected acts into list entries, leaving out what the curator already holds.

    Args:
        acts: Acts keyed by CELEX number.
        list_language: Language of the public label and the ``title``.
        languages: The list's languages.
        curated_celex: CELEX numbers of the hand-curated entries, which are not regenerated.

    Returns:
        Entries sorted by year, kind and number.
    """
    skip = set(curated_celex)
    entries: list[dict[str, Any]] = []
    counted: dict[str, int] = {}
    for act in acts.values():
        category = act.category
        if category is None or act.celex in skip:
            counted[act.resource_type or "?"] = counted.get(act.resource_type or "?", 0) + 1
            continue
        if act.omnibus:
            log.info(
                "left out %s: amends %d acts and is based on none of the base instruments (%s)",
                act.celex,
                act.amends_count,
                (act.titles.get("en") or "")[:80],
            )
            counted["omnibus"] = counted.get("omnibus", 0) + 1
            continue
        citation = parse_citation(act)
        if citation is None:
            counted["non-standard celex"] = counted.get("non-standard celex", 0) + 1
            continue
        title = act.titles.get(list_language) or act.titles.get("en") or ""
        entry: dict[str, Any] = {
            "id": f"{list_language}-{act.celex.lower()}",
            "term": short_citation(citation, category, list_language),
            "lang": list_language,
            "category": category,
            "match": "phrase",
            "aliases": citation_forms(citation, category, languages),
            "celex": act.celex,
            "derived_from": ",".join(sorted(act.bases)),
        }
        if title:
            entry["title"] = title
        entries.append(entry)
    for kind, count in sorted(counted.items()):
        log.info("left out %d related documents of type %s", count, kind)
    entries.sort(
        key=lambda entry: (entry["celex"][1:5], entry["celex"][5], int(entry["celex"][6:10]))
    )
    return entries


def rewrite_list(path: Path, cache: Path, *, refresh: bool) -> None:
    """Regenerate the derived entries of one list file in place.

    Args:
        path: The list file, which must already exist with its hand-curated entries.
        cache: CSV cache directory.
        refresh: Ignore the cache.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    languages = [code for code in document["languages"] if code in LANGUAGE_URI]
    list_language = languages[0]
    curated = [entry for entry in document["terms"] if "derived_from" not in entry]
    acts = collect(languages, cache, refresh=refresh)
    generated = generate_entries(
        acts,
        list_language=list_language,
        languages=languages,
        curated_celex=[entry["celex"] for entry in curated if "celex" in entry],
    )
    document["terms"] = [*curated, *generated]
    sources = [s for s in document.get("sources", []) if s.get("name") != "CELLAR"]
    sources.append(
        {
            "name": "CELLAR",
            "retrieved": datetime.now(tz=UTC).date().isoformat(),
            "description": (
                "Every act CELLAR records as based on, amending or correcting "
                + ", ".join(BASE_INSTRUMENTS)
                + ", enumerated by scripts/legislation_from_cellar.py; legislation types only."
            ),
        }
    )
    document["sources"] = sources
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log.info(
        "%s: %d hand-curated and %d generated entries, %d aliases in all",
        path.name,
        len(curated),
        len(generated),
        sum(len(entry.get("aliases", ())) for entry in document["terms"]),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments, defaulting to the process's.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "lists", nargs="+", type=Path, help="legislation list file(s) to regenerate"
    )
    parser.add_argument(
        "--cache", type=Path, default=Path(".cache/cellar"), help="CSV cache directory"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="ignore the cache and ask CELLAR again"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")
    for path in args.lists:
        rewrite_list(path, args.cache, refresh=args.refresh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
