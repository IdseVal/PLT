"""Stage 1: the legislation matcher.

Neither source endpoint offers a topical filter for pesticides, so every candidate is
selected here, client-side, by looking for the names of pesticide legislation in its text
(``docs/CORE_DOCUMENT.md`` section 2.5). This module therefore decides what is and is not in
the database: it is judged on precision, on recall and on speed, since it runs over the full
text of every judgment fetched.

The lists live in ``data/legislation/``, are located through
:meth:`plt.config.Settings.legislation_list_path` and never by a hard-coded path, and are
validated against ``data/legislation/schema.json`` when loaded. They are curated data owned
by the content manager: nothing here hard-codes an instrument, and an invalid list fails
loudly rather than quietly matching less than the curator intended.

Matching strategy
-----------------
Every literal term and alias of a list is compiled **once**, when the list is loaded, into a
handful of *trie* patterns - one per combination of case sensitivity and word-boundary
requirement. A trie shares the common prefixes of all its literals, which is what keeps the
cost of a scan proportional to the length of the text rather than to the number of
instruments: two thousand implementing regulations, each cited a dozen ways, deepen the trie
but do not add twenty thousand comparisons per character, as a flat alternation would.
Terms declared ``match: regex`` stay outside the tries and are scanned individually, since
an arbitrary curator-supplied expression cannot be merged into one.

Text is compared diacritic-folded, and case-folded unless a term is ``case_sensitive``, so
``Regeling toelating bestrijdingsmiddelen`` is found however a court capitalises it while
``Wgb`` keeps its casing. Both transformations are length-preserving translations, so every
offset and snippet still points into the document's own text, and matching runs without
``re.IGNORECASE`` - which alone costs roughly a factor of fifty on a megabyte of text.

Selection
---------
**A document passes when any instrument on the list is named in it.** There is no score, no
threshold, no gate and no exclusion pattern: an instrument either belongs on the list, in
which case a judgment that names it is a pesticide case, or it does not. The word-search
design that preceded this one selected on substances, product classes, crops and practices
as well as statutes, and needed gates and vetoes to hold its precision; a list of laws needs
neither, because a law's name is not an ordinary word.

Matches are labels
------------------
Every match is reported as a :class:`~plt.pipeline.filters.base.TermMatch` carrying the term
id, **the instrument's short citation and its category**, the list version and the field.
Those rows populate ``keyword_match`` (``docs/architecture.md`` section 3), and they are what
a case is publicly listed under and what the case list is filtered by. A case is labelled
with the *curated* citation rather than the form found in the text, so every spelling of
Regulation (EC) No 1107/2009 files under one name.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal, TypeAlias, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from plt.config import Settings, get_settings
from plt.pipeline.filters.base import Filter, FilterableDocument, FilterResult, TermMatch

__all__ = [
    "DEFAULT_SNIPPET_RADIUS",
    "SCHEMA_FILENAME",
    "Instrument",
    "LegislationFilter",
    "LegislationList",
    "LegislationListError",
    "LegislationListNotFoundError",
    "LegislationListValidationError",
    "fold_diacritics",
    "load_legislation_list",
    "load_legislation_list_for",
]

logger = logging.getLogger(__name__)

#: Name of the JSON Schema file inside the configured legislation directory.
SCHEMA_FILENAME: Final[str] = "schema.json"

#: Characters of context kept either side of a match in a reported snippet.
DEFAULT_SNIPPET_RADIUS: Final[int] = 90

#: Number of selecting term ids named in a result's reason before it is abbreviated.
_REASON_TERM_LIMIT: Final[int] = 8

#: Number of schema violations reported before the message is abbreviated.
_ERROR_LIMIT: Final[int] = 10

#: The ``match`` modes ``schema.json`` allows.
MatchMode = Literal["word", "phrase", "substring", "regex"]

_MATCH_MODES: Final[frozenset[str]] = frozenset({"word", "phrase", "substring", "regex"})

#: Shortest literal a ``substring`` term may carry, term or alias. ``substring`` requires no
#: word boundary on either side, which is what finds a name inside a compound - and equally
#: what finds a fragment inside a word that has nothing to do with it. Six is where the
#: measurement put the line under the earlier keyword lists: over 150,000 sampled
#: Rechtspraak judgments and their 947,625 distinct word forms, every four- and
#: five-character literal was reached inside an ordinary word, while from six characters the
#: containing word is almost always the term's own compound or inflection. A statute name is
#: rarely short enough for this to bite, and the floor stays because the loader cannot know
#: what the next curator will type.
_MIN_SUBSTRING_LENGTH: Final[int] = 6

#: Upper bound of the code point range scanned when building the folding tables. Nothing
#: above it decomposes to a single Latin-style base character.
_FOLD_TABLE_LIMIT: Final[int] = 0x3000

_WORD_CHAR: Final[re.Pattern[str]] = re.compile(r"\w", re.UNICODE)
_WHITESPACE_RUN: Final[re.Pattern[str]] = re.compile(r"\s+")

#: A prefix tree over the characters of the literals in one bucket. The empty-string key
#: marks the end of a literal.
_TrieNode: TypeAlias = dict[str, "_TrieNode"]

#: Bucket key: case sensitivity, and whether a word boundary is required on each side.
_BucketKey: TypeAlias = tuple[bool, bool, bool]


class LegislationListError(Exception):
    """Base class for every failure to load or use a legislation list."""


class LegislationListNotFoundError(LegislationListError):
    """A jurisdiction has no legislation list file.

    A jurisdiction cannot be onboarded before its list exists (``docs/CORE_DOCUMENT.md``
    section 2.5), so this is an onboarding error, not a reason to fall back to matching
    nothing.
    """


class LegislationListValidationError(LegislationListError):
    """A legislation list is malformed, invalid against the schema or inconsistent."""


def _build_fold_table(*, lower: bool) -> Mapping[int, str]:
    """Build a length-preserving translation table for matching.

    A character folds when its canonical decomposition is exactly one base character plus
    combining marks - ``ï`` to ``i``, ``é`` to ``e``, ``ü`` to ``u``. Characters whose
    decomposition is longer or absent are left alone, so ``ß`` stays ``ß``. Case folding is
    applied on top only where the lowercase form is a single character, for the same reason:
    the table must never change the length of the string it is applied to.

    Args:
        lower: Whether to case-fold as well as strip diacritics.

    Returns:
        Mapping of code point to replacement character.
    """
    table: dict[int, str] = {}
    for code_point in range(0x80, _FOLD_TABLE_LIMIT):
        character = chr(code_point)
        decomposed = unicodedata.normalize("NFD", character)
        base = "".join(part for part in decomposed if not unicodedata.combining(part))
        replacement = base if len(base) == 1 else character
        if lower:
            lowered = replacement.lower()
            if len(lowered) == 1:
                replacement = lowered
        if replacement != character:
            table[code_point] = replacement
    if lower:
        table.update({code_point: chr(code_point).lower() for code_point in range(0x41, 0x5B)})
    return table


@lru_cache(maxsize=2)
def _fold_table(*, lower: bool) -> Mapping[int, str]:
    """Return the folding table, built once per process for each variant."""
    return _build_fold_table(lower=lower)


def fold_diacritics(text: str, *, lower: bool = False) -> str:
    """Strip diacritics from text, optionally case-folding, without changing its length.

    Length preservation is the point: patterns are matched against the folded string while
    offsets and snippets are taken from the unfolded text, so a match on ``reglement`` still
    reports the document's own ``règlement``.

    Args:
        text: Text to fold. Should already be NFC-normalised; a combining mark standing on
            its own is left in place, since removing it would shift every later offset.
        lower: Whether to case-fold as well, for case-insensitive matching.

    Returns:
        The folded text, of exactly the same length as the input.
    """
    return text.translate(_fold_table(lower=lower))


def _normalise(text: str) -> str:
    """Return the NFC form of text, avoiding a copy when it is normalised already."""
    if unicodedata.is_normalized("NFC", text):
        return text
    return unicodedata.normalize("NFC", text)


def _collapse_whitespace(text: str) -> str:
    """Collapse every run of whitespace to a single space and strip the ends."""
    return _WHITESPACE_RUN.sub(" ", text).strip()


@dataclass(frozen=True, slots=True)
class Instrument:
    """One instrument on a list, with the forms it is cited under.

    Attributes:
        term_id: Stable id, e.g. ``nl-wgb``. Match provenance is stored against it.
        term: The instrument's short citation as a court writes it, in the source language.
        label: What a reader is shown when this instrument matches. Defaults to
            :attr:`term`, and exists for the case where the two cannot be the same thing: a
            ``regex`` term's ``term`` is a pattern, and a pattern is not a name.
        lang: ISO 639-1 code of the language ``term`` is written in.
        category: The kind of instrument this is. Stored on every match and shown publicly,
            so it is one of the two labels a case is listed under.
        match: How the term is matched: ``word``, ``phrase``, ``substring`` or ``regex``.
        case_sensitive: Whether casing must match, for acronyms whose lowercase form is a
            common word.
        case_sensitive_exception: Whether the curator has knowingly opted this term out of
            the acronym-only rule that :func:`_check_consistency` enforces under
            ``case_sensitive``.
        aliases: The instrument's other citation forms and names, in any language the list
            carries. They report :attr:`term_id` and are labelled with :attr:`term`, so a case
            is listed under the curated citation whichever form the court used.
        celex: CELEX number of a Union instrument, as provenance. Nothing matches on it.
        bwb: Basiswettenbestand identifier of a Dutch instrument, as provenance.
        title: The instrument's full official title, for a reader of the list.
        note: The curator's rationale, if recorded.
    """

    term_id: str
    term: str
    label: str | None
    lang: str
    category: str
    match: MatchMode
    case_sensitive: bool
    case_sensitive_exception: bool
    aliases: tuple[str, ...]
    celex: str | None
    bwb: str | None
    title: str | None
    note: str | None

    @property
    def public_label(self) -> str:
        """Return what a reader is shown when this instrument matches.

        Returns:
            The curated ``label`` where there is one, and the term itself otherwise. Every
            match carries this rather than the matched text, so a case is listed under one
            name however many citation forms the judgment used.
        """
        return self.label or self.term

    @property
    def patterns(self) -> tuple[str, ...]:
        """Return the term and its aliases, which are interchangeable for matching."""
        return (self.term, *self.aliases)


@dataclass(frozen=True, slots=True)
class _PatternSpec:
    """One literal to compile, and who to credit when it matches.

    Attributes:
        owner: Term id credited on a match.
        literal: The text to match.
        mode: The declared match mode.
        case_sensitive: Whether casing must match.
    """

    owner: str
    literal: str
    mode: MatchMode
    case_sensitive: bool


@dataclass(frozen=True, slots=True)
class _Bucket:
    """One compiled trie and the owners of every literal in it.

    A bucket holds the literals that share a case sensitivity and a boundary requirement, so
    that both can be applied to the trie as a whole. The matched text identifies the term:
    the trie only ever matches strings that were inserted into it, so its whitespace-collapsed
    form is a key of :attr:`owners`.

    Attributes:
        pattern: The compiled trie, boundaries included.
        owners: Normalised literal to the ids of every term that declared it. Two instruments
            that happen to share a citation form are both credited by it.
        case_sensitive: Whether the bucket scans the case-folded text or the cased one.
    """

    pattern: re.Pattern[str]
    owners: Mapping[str, tuple[str, ...]]
    case_sensitive: bool

    def scan(self, cased: str, folded: str) -> Iterator[tuple[tuple[str, ...], int, int]]:
        """Yield every occurrence in the text, overlaps included.

        Scanning resumes one character past the start of a hit rather than past its end, so a
        term nested inside a longer one is still found. Within one starting position the trie
        matches greedily, so the longest - the most specific - literal wins.

        Args:
            cased: Diacritic-folded text with its casing intact.
            folded: The same text, case-folded as well.

        Yields:
            Tuples of owner keys, start offset and end offset.
        """
        haystack = cased if self.case_sensitive else folded
        search = self.pattern.search
        owners_of = self.owners.get
        position = 0
        limit = len(haystack)
        while position <= limit:
            found = search(haystack, position)
            if found is None:
                return
            owners = owners_of(_collapse_whitespace(found.group()))
            if owners is not None:
                yield owners, found.start(), found.end()
            position = found.start() + 1


@dataclass(frozen=True, slots=True)
class _PatternSet:
    """Everything one list matches with: its trie buckets and any regex patterns.

    Attributes:
        buckets: One trie per case sensitivity and boundary requirement.
        regexes: Curator-supplied expressions, scanned one at a time because an arbitrary
            expression cannot be merged into a trie.
    """

    buckets: tuple[_Bucket, ...]
    regexes: tuple[tuple[str, re.Pattern[str]], ...]

    @property
    def size(self) -> int:
        """Return the number of distinct literals and expressions compiled."""
        return sum(len(bucket.owners) for bucket in self.buckets) + len(self.regexes)

    def scan(self, cased: str, folded: str) -> Iterator[tuple[tuple[str, ...], int, int]]:
        """Yield every occurrence of every pattern in the set.

        Args:
            cased: Diacritic-folded text with its casing intact.
            folded: The same text, case-folded as well.

        Yields:
            Tuples of owner keys, start offset and end offset.
        """
        for bucket in self.buckets:
            yield from bucket.scan(cased, folded)
        for owner, pattern in self.regexes:
            for found in pattern.finditer(cased):
                yield (owner,), found.start(), found.end()


@dataclass(slots=True)
class _Hit:
    """Accumulated occurrences of one term in one field.

    Counted rather than collected: a citation repeated a thousand times in a full text must
    cost a counter, not a thousand objects.

    Attributes:
        occurrences: How often the term matched this field.
        start: Start offset of the first occurrence.
        end: End offset of the first occurrence.
        matched_text: The first occurrence verbatim.
        snippet: Context around the first occurrence.
    """

    occurrences: int
    start: int
    end: int
    matched_text: str
    snippet: str


@dataclass(frozen=True, slots=True)
class LegislationList:
    """A jurisdiction's legislation list, loaded, validated and compiled.

    Building one is the expensive part - reading, schema validation, pattern compilation -
    and happens once per list per process, never per document.

    Attributes:
        jurisdiction: Jurisdiction code, ``NL`` or ``EU``.
        jurisdiction_name: Human-readable jurisdiction name.
        list_version: Semantic version of the list, stored with every match.
        updated: ISO 8601 date the list was last curated.
        languages: ISO 639-1 codes the list carries names in.
        fields: Document fields an instrument is looked for in, in curation order. A field
            absent from this tuple is not scanned at all: the curator decides what counts as
            the text of a judgment.
        terms: The instruments, keyed by id, in file order.
        source_path: File the list was read from.
        digest: SHA-256 of the file's bytes, so a run can record exactly which list it
            applied rather than only which version the file claimed to be.
        patterns: Compiled patterns.
    """

    jurisdiction: str
    jurisdiction_name: str
    list_version: str
    updated: str
    languages: tuple[str, ...]
    fields: tuple[str, ...]
    terms: Mapping[str, Instrument]
    source_path: Path
    digest: str
    patterns: _PatternSet

    @property
    def term_count(self) -> int:
        """Return the number of instruments, aliases excluded."""
        return len(self.terms)

    @property
    def pattern_count(self) -> int:
        """Return the number of distinct literals compiled, aliases included."""
        return self.patterns.size

    @property
    def scan_count(self) -> int:
        """Return how many passes over a document one evaluation costs."""
        return len(self.patterns.buckets) + len(self.patterns.regexes)

    @property
    def categories(self) -> tuple[str, ...]:
        """Return every category the list uses, sorted, for the public filter's options."""
        return tuple(sorted({term.category for term in self.terms.values()}))


def _is_word_character(character: str) -> bool:
    """Return whether a character counts as a word character for boundary purposes."""
    return _WORD_CHAR.match(character) is not None


def _trie_insert(root: _TrieNode, literal: str) -> None:
    """Insert one literal into the prefix tree.

    Args:
        root: The tree to insert into, mutated in place.
        literal: The literal to insert, whitespace already collapsed.
    """
    node = root
    for character in literal:
        node = node.setdefault(character, {})
    node[""] = {}


def _trie_pattern(node: _TrieNode) -> str:
    r"""Compile a prefix tree into a regular expression.

    Shared prefixes are emitted once, and characters that end a literal branch collapse into
    a character class, so the engine decides on the current character where to go next
    instead of retrying every term in turn. A space in a literal becomes ``\s+``: a citation
    must still match across the line break a judgment happens to contain.

    Args:
        node: The tree node to compile.

    Returns:
        A regex source string matching every literal below the node, longest first.
    """
    if not node or (len(node) == 1 and "" in node):
        return ""
    optional = "" in node
    alternatives: list[str] = []
    singles: list[str] = []
    for character in sorted(key for key in node if key):
        atom = r"\s+" if character == " " else re.escape(character)
        tail = _trie_pattern(node[character])
        if tail:
            alternatives.append(atom + tail)
        elif character == " ":
            alternatives.append(atom)
        else:
            singles.append(character)
    if len(singles) == 1:
        alternatives.append(re.escape(singles[0]))
    elif singles:
        alternatives.append("[" + "".join(re.escape(one) for one in singles) + "]")
    body = alternatives[0] if len(alternatives) == 1 else "(?:" + "|".join(alternatives) + ")"
    return f"(?:{body})?" if optional else body


def _bucket_key(spec: _PatternSpec, literal: str) -> _BucketKey:
    """Return the bucket a literal belongs in.

    ``word`` and ``phrase`` need word boundaries, but only on an edge that is itself a word
    character: requiring one before the ``1`` of ``1107/2009`` is right, requiring one before
    the leading bracket of ``(EU) 2017/2324`` would be wrong. ``substring`` deliberately
    needs none, which is how a compounding language finds ``Bestrijdingsmiddelenwet`` inside
    ``Bestrijdingsmiddelenwetgeving``.

    Args:
        spec: The literal's specification.
        literal: The literal, whitespace already collapsed.

    Returns:
        Case sensitivity and the boundary requirement on each side.
    """
    if spec.mode == "substring":
        return (spec.case_sensitive, False, False)
    return (
        spec.case_sensitive,
        _is_word_character(literal[0]),
        _is_word_character(literal[-1]),
    )


def _build_pattern_set(specs: Sequence[_PatternSpec]) -> _PatternSet:
    """Compile every literal of one list into its trie buckets and its regex patterns.

    Args:
        specs: The literals to compile, with their owners and modes.

    Returns:
        The compiled pattern set.

    Raises:
        LegislationListValidationError: If a literal is blank or an expression does not
            compile.
    """
    grouped: dict[_BucketKey, dict[str, list[str]]] = {}
    regexes: list[tuple[str, re.Pattern[str]]] = []
    for spec in specs:
        owner_label = f"term {spec.owner!r}"
        if spec.mode == "regex":
            regexes.append((spec.owner, _compile_regex(spec, owner=owner_label)))
            continue
        literal = _collapse_whitespace(
            fold_diacritics(_normalise(spec.literal), lower=not spec.case_sensitive)
        )
        if not literal:
            message = f"{owner_label}: pattern {spec.literal!r} is empty once stripped"
            raise LegislationListValidationError(message)
        owners = grouped.setdefault(_bucket_key(spec, literal), {}).setdefault(literal, [])
        if spec.owner not in owners:
            owners.append(spec.owner)

    buckets: list[_Bucket] = []
    for (case_sensitive, left, right), literals in sorted(grouped.items()):
        root: _TrieNode = {}
        for literal in literals:
            _trie_insert(root, literal)
        prefix = r"(?<!\w)" if left else ""
        suffix = r"(?!\w)" if right else ""
        buckets.append(
            _Bucket(
                pattern=re.compile(f"{prefix}{_trie_pattern(root)}{suffix}", re.UNICODE),
                owners={literal: tuple(owners) for literal, owners in literals.items()},
                case_sensitive=case_sensitive,
            )
        )
    return _PatternSet(buckets=tuple(buckets), regexes=tuple(regexes))


def _compile_regex(spec: _PatternSpec, *, owner: str) -> re.Pattern[str]:
    """Compile one curator-supplied expression.

    Args:
        spec: The literal's specification, whose literal is the expression.
        owner: Description of the owner, used in the error message.

    Returns:
        The compiled expression, matched against the diacritic-folded text.

    Raises:
        LegislationListValidationError: If the expression does not compile.
    """
    flags = re.UNICODE if spec.case_sensitive else re.UNICODE | re.IGNORECASE
    try:
        return re.compile(spec.literal, flags)
    except re.error as error:
        message = f"{owner}: invalid regular expression {spec.literal!r}: {error}"
        raise LegislationListValidationError(message) from error


def _optional_text(raw: Mapping[str, Any], key: str) -> str | None:
    """Return a string field of a term entry, or ``None`` when it is absent or empty."""
    value = raw.get(key)
    return str(value) if value else None


def _term_from_raw(raw: Mapping[str, Any]) -> Instrument:
    """Build a typed instrument from its schema-validated JSON object.

    Args:
        raw: One entry of the list's ``terms`` array.

    Returns:
        The typed instrument.

    Raises:
        LegislationListValidationError: If the match mode is not one the matcher implements.
    """
    match_mode = str(raw.get("match", "word"))
    if match_mode not in _MATCH_MODES:  # pragma: no cover - the schema rejects this first
        message = f"term {raw.get('id')!r}: unknown match mode {match_mode!r}"
        raise LegislationListValidationError(message)
    return Instrument(
        term_id=str(raw["id"]),
        term=str(raw["term"]),
        label=_optional_text(raw, "label"),
        lang=str(raw["lang"]),
        category=str(raw["category"]),
        match=cast(MatchMode, match_mode),
        case_sensitive=bool(raw.get("case_sensitive", False)),
        case_sensitive_exception=bool(raw.get("case_sensitive_exception", False)),
        aliases=tuple(str(alias) for alias in raw.get("aliases", ())),
        celex=_optional_text(raw, "celex"),
        bwb=_optional_text(raw, "bwb"),
        title=_optional_text(raw, "title"),
        note=_optional_text(raw, "note"),
    )


def _offending_term_id(document: object, index: int) -> str:
    """Return the id of the term at an index, for an error message.

    Args:
        document: The parsed list.
        index: Index into its ``terms`` array.

    Returns:
        The term id, or a placeholder when the entry has none.
    """
    if not isinstance(document, dict):  # pragma: no cover - the schema rejects this first
        return "<unknown>"
    terms = document.get("terms")
    if not isinstance(terms, list) or index >= len(terms):  # pragma: no cover - defensive
        return "<unknown>"
    entry = terms[index]
    identifier = entry.get("id") if isinstance(entry, dict) else None
    return str(identifier) if identifier is not None else "<no id>"


def _describe(error: ValidationError, document: object) -> str:
    """Describe one schema violation, naming the offending term where there is one.

    Args:
        error: The violation reported by the validator.
        document: The parsed list, used to look up the id of the offending term.

    Returns:
        A message a curator can act on without reading the schema.
    """
    path = list(error.absolute_path)
    location = "/".join(str(part) for part in path) or "<root>"
    if len(path) >= 2 and path[0] == "terms" and isinstance(path[1], int):
        identifier = _offending_term_id(document, path[1])
        return f"term {identifier!r} (at {location}): {error.message}"
    return f"at {location}: {error.message}"


def _validate(document: object, schema_path: Path) -> None:
    """Validate a parsed list against ``schema.json``, reporting every violation.

    Args:
        document: The parsed JSON document.
        schema_path: Path to the schema file.

    Raises:
        LegislationListError: If the schema file is missing or is not valid JSON.
        LegislationListValidationError: If the document violates the schema.
    """
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        message = f"legislation list schema not found at {schema_path}"
        raise LegislationListError(message) from error
    except json.JSONDecodeError as error:
        message = f"legislation list schema at {schema_path} is not valid JSON: {error}"
        raise LegislationListError(message) from error

    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path))
    if not errors:
        return
    detail = "; ".join(_describe(error, document) for error in errors[:_ERROR_LIMIT])
    if len(errors) > _ERROR_LIMIT:
        detail = f"{detail}; and {len(errors) - _ERROR_LIMIT} further violation(s)"
    message = f"invalid legislation list: {detail}"
    raise LegislationListValidationError(message)


def _is_acronym_shaped(literal: str) -> bool:
    """Return whether case sensitivity can be applied to a literal without losing renderings.

    The test is on character class, not on length, because that is what the failure is about.
    A literal carrying a lowercase letter has renderings the curated string will not match -
    sentence-initially, in a heading, in the capitalisation a court happens to choose - and
    ``case_sensitive`` silently drops every one of them. A literal written in capitals or in
    digits alone reads the same wherever it appears and loses nothing.

    Whitespace disqualifies a literal for the same reason: a spelled-out name is prose
    whichever case it is typed in, so upper-casing one is not a way to satisfy the rule.

    Args:
        literal: The term or alias as the curator wrote it.

    Returns:
        True if no rendering of the literal is lost to case sensitivity.
    """
    stripped = literal.strip()
    if not stripped or _WHITESPACE_RUN.search(stripped):
        return False
    return not any(character.islower() for character in stripped)


def _check_case_sensitivity(term: Instrument) -> None:
    """Enforce the acronym-only rule that ``case_sensitive`` has always carried.

    ``case_sensitive`` is declared on a term and inherited by every one of its aliases, which
    the schema cannot express and therefore cannot check. An ordinary word that inherits it
    matches only the exact string as curated and matches nothing on every other casing
    (``data/legislation/README.md``, "Case sensitivity").

    ``regex`` terms are exempt: their pattern is an expression rather than a literal, and its
    lowercase characters are syntax - a negative lookbehind for a word character - not prose.

    Args:
        term: The term to check.

    Raises:
        LegislationListValidationError: If a ``case_sensitive`` literal is not acronym-shaped
            and the term does not declare ``case_sensitive_exception``, or if the term
            declares that exception without needing it.
    """
    if not term.case_sensitive:
        if term.case_sensitive_exception:
            message = (
                f"term {term.term_id!r}: declares case_sensitive_exception but is not "
                "case_sensitive, so it opts out of a rule that does not apply to it"
            )
            raise LegislationListValidationError(message)
        return
    if term.match == "regex":
        if term.case_sensitive_exception:
            message = (
                f"term {term.term_id!r}: declares case_sensitive_exception but is matched as "
                "a regular expression, which the acronym rule does not reach"
            )
            raise LegislationListValidationError(message)
        return
    offenders = [literal for literal in term.patterns if not _is_acronym_shaped(literal)]
    if not offenders:
        if term.case_sensitive_exception:
            message = (
                f"term {term.term_id!r}: declares case_sensitive_exception but every literal "
                "it carries is an acronym; remove the exception rather than leave one "
                "standing that nothing needs"
            )
            raise LegislationListValidationError(message)
        return
    if term.case_sensitive_exception:
        return
    offender = offenders[0]
    role = "term" if offender == term.term else "alias"
    message = (
        f"term {term.term_id!r}: case_sensitive {role} {offender!r} is not an acronym. "
        "case_sensitive is inherited by a term and every one of its aliases, so an ordinary "
        "word carried by an acronym term matches only the casing curated here and nothing "
        "on every other - including the sentence-initial one. Carry it as its own "
        'case-insensitive term (data/legislation/README.md, "Case sensitivity")'
    )
    raise LegislationListValidationError(message)


def _check_substring_length(term: Instrument) -> None:
    """Reject a ``substring`` literal short enough to be reached inside an unrelated word.

    ``match`` is inherited by every alias exactly as ``case_sensitive`` is, and ``substring``
    deliberately requires no word boundary on either side, which is what lets a compounding
    language find a statute's name inside a longer word. Below
    :data:`_MIN_SUBSTRING_LENGTH` characters that same absence of a boundary reaches into
    words that have nothing to do with the term, and the fragment alone selects the document.

    Args:
        term: The term to check.

    Raises:
        LegislationListValidationError: If a literal of a ``substring`` term is too short.
    """
    if term.match != "substring":
        return
    for literal in term.patterns:
        stripped = literal.strip()
        if len(stripped) >= _MIN_SUBSTRING_LENGTH:
            continue
        role = "term" if literal == term.term else "alias"
        message = (
            f"term {term.term_id!r}: substring {role} {stripped!r} is {len(stripped)} "
            f"characters, below the {_MIN_SUBSTRING_LENGTH} a substring literal needs. "
            "match is inherited by a term and every one of its aliases, and substring "
            "requires no word boundary, so a literal this short selects documents on a "
            "fragment of an unrelated word. Match it as a word, in a term of its own if the "
            'parent needs substring (data/legislation/README.md, "Match modes")'
        )
        raise LegislationListValidationError(message)


def _check_consistency(terms: Sequence[Instrument]) -> None:
    """Check what the schema cannot: duplicate ids and inherited attributes.

    ``case_sensitive`` and ``match`` are declared once and inherited by every alias of the
    term. The schema sees one value and cannot ask whether it fits each literal it reaches,
    so the two rules that make inheritance safe are enforced here: a ``case_sensitive`` term
    carries acronyms only, and a ``substring`` term carries no literal short enough to be
    found inside an unrelated word.

    Args:
        terms: The terms of one list, in file order.

    Raises:
        LegislationListValidationError: If an id repeats, if a ``case_sensitive`` literal is
            an ordinary word, or if a ``substring`` literal is too short.
    """
    seen: set[str] = set()
    for term in terms:
        if term.term_id in seen:
            message = f"term {term.term_id!r}: duplicate term id"
            raise LegislationListValidationError(message)
        seen.add(term.term_id)
    for term in terms:
        _check_case_sensitivity(term)
        _check_substring_length(term)


def _read_list(path: Path) -> tuple[object, str]:
    """Read and parse one list file.

    Args:
        path: Path to the list file.

    Returns:
        The parsed JSON document and the SHA-256 digest of the file's bytes.

    Raises:
        LegislationListNotFoundError: If the file does not exist.
        LegislationListValidationError: If the file is not valid JSON.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError as error:
        message = (
            f"no legislation list at {path}; a jurisdiction cannot be ingested before its "
            "list exists (core document section 2.5)"
        )
        raise LegislationListNotFoundError(message) from error
    try:
        return json.loads(data.decode("utf-8")), hashlib.sha256(data).hexdigest()
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        message = f"legislation list at {path} is not valid JSON: {error}"
        raise LegislationListValidationError(message) from error


def load_legislation_list(path: Path, *, schema_path: Path | None = None) -> LegislationList:
    """Read, validate and compile one jurisdiction's legislation list.

    Validation is strict and loud by design: a list that violates
    ``data/legislation/schema.json`` or repeats an id raises an error naming the offending
    term, rather than silently matching less.

    Args:
        path: Path to the list file.
        schema_path: Path to the schema. Defaults to ``schema.json`` beside the list.

    Returns:
        The compiled list, ready to match documents.

    Raises:
        LegislationListNotFoundError: If the list file does not exist.
        LegislationListValidationError: If the file is not valid JSON, violates the schema or
            breaks a cross-term rule.
        LegislationListError: If the schema file itself cannot be read.
    """
    document, digest = _read_list(path)
    _validate(document, schema_path if schema_path is not None else path.parent / SCHEMA_FILENAME)
    raw = cast(dict[str, Any], document)

    terms = [_term_from_raw(entry) for entry in raw["terms"]]
    _check_consistency(terms)
    term_specs = [
        _PatternSpec(
            owner=term.term_id,
            literal=literal,
            mode=term.match,
            case_sensitive=term.case_sensitive,
        )
        for term in terms
        for literal in term.patterns
    ]

    legislation_list = LegislationList(
        jurisdiction=str(raw["jurisdiction"]),
        jurisdiction_name=str(raw["jurisdiction_name"]),
        list_version=str(raw["list_version"]),
        updated=str(raw["updated"]),
        languages=tuple(str(code) for code in raw["languages"]),
        fields=tuple(str(name) for name in raw["fields"]),
        terms={term.term_id: term for term in terms},
        source_path=path,
        digest=digest,
        patterns=_build_pattern_set(term_specs),
    )
    logger.info(
        "Loaded legislation list %s v%s (sha256 %s) from %s: %d instruments, %d literals, "
        "%d scans per document; any instrument selects; fields %s",
        legislation_list.jurisdiction,
        legislation_list.list_version,
        digest[:12],
        path.name,
        legislation_list.term_count,
        legislation_list.pattern_count,
        legislation_list.scan_count,
        ", ".join(legislation_list.fields),
    )
    return legislation_list


@lru_cache(maxsize=32)
def _load_cached(path: Path, schema_path: Path, fingerprint: tuple[int, int]) -> LegislationList:
    """Load a list, memoised on the file's path and modification fingerprint.

    Args:
        path: Path to the list file.
        schema_path: Path to the schema file.
        fingerprint: Size and modification time of the list, so an edited file is recompiled
            rather than served stale from the cache.

    Returns:
        The compiled list.
    """
    del fingerprint  # Part of the cache key only.
    return load_legislation_list(path, schema_path=schema_path)


def load_legislation_list_for(
    jurisdiction_code: str, settings: Settings | None = None
) -> LegislationList:
    """Load a jurisdiction's legislation list, compiling it at most once per process.

    Args:
        jurisdiction_code: Jurisdiction code such as ``NL`` or ``EU``, case-insensitive.
        settings: Settings resolving the legislation directory. Defaults to the process-wide
            settings.

    Returns:
        The compiled list for that jurisdiction.

    Raises:
        LegislationListNotFoundError: If the jurisdiction has no list file.
        LegislationListValidationError: If the list is invalid.
    """
    resolved = settings if settings is not None else get_settings()
    path = resolved.legislation_list_path(jurisdiction_code)
    try:
        stat = path.stat()
    except OSError as error:
        message = (
            f"no legislation list for jurisdiction {jurisdiction_code!r} at {path}; a "
            "jurisdiction cannot be ingested before its list exists (core document 2.5)"
        )
        raise LegislationListNotFoundError(message) from error
    schema_path = resolved.legislation_dir / SCHEMA_FILENAME
    return _load_cached(path, schema_path, (stat.st_size, stat.st_mtime_ns))


class LegislationFilter(Filter):
    """Filter stage 1: look for one jurisdiction's pesticide legislation in a document.

    The list is compiled when the stage is constructed, so evaluating a document costs a
    handful of scans of its text whatever the size of the list - see
    :attr:`LegislationList.scan_count`.

    Attributes:
        name: Stage name reported on every result.
    """

    name = "legislation"

    def __init__(
        self,
        legislation_list: LegislationList,
        *,
        snippet_radius: int = DEFAULT_SNIPPET_RADIUS,
    ) -> None:
        """Bind the stage to a compiled list.

        Args:
            legislation_list: The compiled list to match against.
            snippet_radius: Characters of context kept either side of a reported match.

        Raises:
            ValueError: If the snippet radius is negative.
        """
        if snippet_radius < 0:
            message = f"snippet_radius must not be negative, got {snippet_radius}"
            raise ValueError(message)
        self._list = legislation_list
        self._snippet_radius = snippet_radius

    @classmethod
    def for_jurisdiction(
        cls,
        jurisdiction_code: str,
        *,
        settings: Settings | None = None,
        snippet_radius: int = DEFAULT_SNIPPET_RADIUS,
    ) -> LegislationFilter:
        """Build the stage for a jurisdiction, loading its list through the settings.

        Args:
            jurisdiction_code: Jurisdiction code such as ``NL`` or ``EU``.
            settings: Settings resolving the legislation directory.
            snippet_radius: Characters of context kept either side of a reported match.

        Returns:
            A stage bound to that jurisdiction's list.

        Raises:
            LegislationListNotFoundError: If the jurisdiction has no list file.
        """
        return cls(
            load_legislation_list_for(jurisdiction_code, settings),
            snippet_radius=snippet_radius,
        )

    @property
    def legislation_list(self) -> LegislationList:
        """Return the compiled list this stage matches against."""
        return self._list

    def evaluate(self, case: FilterableDocument) -> FilterResult:
        """Look for the list's instruments in one document and decide whether it is in scope.

        Only the fields named in the list's ``fields`` are read, and a field the document
        does not carry is treated as absent, so a source without an abstract needs no
        adapter. Fields are scanned one at a time and only counters are kept, so peak memory
        stays proportional to the largest field rather than to the number of matches.

        Args:
            case: The normalised document to judge.

        Returns:
            The verdict, carrying every instrument found and a readable reason.
        """
        hits: dict[str, dict[str, _Hit]] = {}
        for field_name in self._list.fields:
            text = getattr(case, field_name, None)
            if not isinstance(text, str) or not text:
                continue
            reference = _normalise(text)
            cased = fold_diacritics(reference)
            folded = fold_diacritics(reference, lower=True)
            self._collect(cased, folded, reference, field_name, hits)

        return self._select(hits)

    def _collect(
        self,
        cased: str,
        folded: str,
        reference: str,
        field_name: str,
        hits: dict[str, dict[str, _Hit]],
    ) -> None:
        """Accumulate every occurrence found in one field.

        Args:
            cased: Diacritic-folded field text with its casing intact.
            folded: The same text, case-folded as well.
            reference: The same text unfolded, from which evidence is taken.
            field_name: Name of the field being scanned.
            hits: Accumulator keyed by term id and then field name; mutated in place.
        """
        for owners, start, end in self._list.patterns.scan(cased, folded):
            for term_id in owners:
                per_field = hits.setdefault(term_id, {})
                existing = per_field.get(field_name)
                if existing is None:
                    per_field[field_name] = _Hit(
                        occurrences=1,
                        start=start,
                        end=end,
                        matched_text=reference[start:end],
                        snippet=self._snippet(reference, start, end),
                    )
                else:
                    existing.occurrences += 1

    def _snippet(self, reference: str, start: int, end: int) -> str:
        """Render the context around a match, casing and diacritics intact.

        Args:
            reference: The document field text the offsets refer to.
            start: Start offset of the match.
            end: End offset of the match.

        Returns:
            A whitespace-collapsed snippet, elided where it was cut out of a longer text.
        """
        radius = self._snippet_radius
        left = max(0, start - radius)
        right = min(len(reference), end + radius)
        snippet = _collapse_whitespace(reference[left:right])
        prefix = "…" if left > 0 else ""
        suffix = "…" if right < len(reference) else ""
        return f"{prefix}{snippet}{suffix}"

    def _select(self, hits: Mapping[str, Mapping[str, _Hit]]) -> FilterResult:
        """Turn the occurrences into a verdict: any instrument found selects the document.

        Args:
            hits: Occurrences per term id and field, as collected from the document.

        Returns:
            The verdict, with one match per instrument and field it was found in.
        """
        matches: list[TermMatch] = []
        for term_id, per_field in hits.items():
            term = self._list.terms[term_id]
            matches.extend(
                TermMatch(
                    term_id=term_id,
                    term=term.public_label,
                    category=term.category,
                    list_version=self._list.list_version,
                    field=field_name,
                    snippet=hit.snippet,
                    matched_text=hit.matched_text,
                    occurrences=hit.occurrences,
                    start=hit.start,
                    end=hit.end,
                )
                for field_name, hit in per_field.items()
            )
        selectors = list(hits)
        return FilterResult(
            passed=bool(selectors),
            reason=self._reason(selectors),
            stage=self.name,
            matches=tuple(matches),
        )

    def _reason(self, selectors: Sequence[str]) -> str:
        """Phrase the verdict for the pipeline report.

        Args:
            selectors: Ids of the instruments that selected the document.

        Returns:
            A one-line explanation naming the list and the instruments found. It has to
            stand on its own in a match report and on a methodology page, so it says which
            instruments did the selecting rather than only how many.
        """
        provenance = f"{self._list.jurisdiction} list v{self._list.list_version}"
        if not selectors:
            return f"no listed instrument named ({provenance})"
        listing = ", ".join(selectors[:_REASON_TERM_LIMIT])
        if len(selectors) > _REASON_TERM_LIMIT:
            listing = f"{listing} and {len(selectors) - _REASON_TERM_LIMIT} more"
        return f"named {len(selectors)} listed instrument(s) ({provenance}): {listing}"
