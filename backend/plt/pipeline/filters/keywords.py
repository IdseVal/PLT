"""Stage 1: the curated keyword matcher.

Neither source endpoint offers a topical filter for pesticides, so every candidate is
selected here, client-side, by matching curated terms against its text
(``docs/core-document.md`` section 2.5). This module therefore decides what is and is not in
the database: it is judged on precision, on recall and on speed, since it runs over the full
text of every judgment fetched.

The lists live in ``data/keywords/``, are located through
:meth:`plt.config.Settings.keyword_list_path` and never by a hard-coded path, and are
validated against ``data/keywords/schema.json`` when loaded. They are curated data owned by
the content manager: nothing here hard-codes a term, and an invalid list fails loudly rather
than quietly matching less than the curator intended.

Matching strategy
-----------------
Every literal term and alias of a list is compiled **once**, when the list is loaded, into a
handful of *trie* patterns - one per combination of case sensitivity and word-boundary
requirement, so three or four for the shipped lists. A trie shares the common prefixes of
all its terms, which is what keeps the cost of a scan proportional to the length of the text
rather than to the number of terms: adding a hundred more terms deepens the trie but does
not add a hundred more comparisons per character, as a flat alternation of a hundred
alternatives would. Terms declared ``match: regex`` stay outside the tries and are scanned
individually, since an arbitrary curator-supplied expression cannot be merged into one; the
shipped lists use none.

Text is compared diacritic-folded, and case-folded unless a term is ``case_sensitive``, so
``neonicotinoide`` finds ``neonicotinoïde`` while ``DDT`` and ``Ctgb`` keep their casing.
Both transformations are length-preserving translations, so every offset and snippet still
points into the document's own text, and matching runs without ``re.IGNORECASE`` - which
alone costs roughly a factor of fifty on a megabyte of text.

Selection and review
--------------------
A document passes when its score reaches the list's ``min_score``, and that threshold is
deliberately low: the PLT optimises for recall, and a threshold raised to buy precision buys
it with cases that then silently do not exist (``docs/core-document.md`` section 2.7).
Precision is bought downstream instead. A document that passes but scores inside the list's
``review_band`` - the interval immediately above ``min_score``, where both live dry runs
found the false positives concentrated - is admitted exactly like any other and additionally
marked ``needs_review``, for a content manager to confirm or reject. The band is per list,
because the EU evidence does not transfer to the Dutch corpus, and it is data in the same
curated file as the terms it qualifies.

Every match is reported as a :class:`~plt.pipeline.filters.base.TermMatch` carrying the term
id, the list version, the field and the weight actually applied. Those rows populate
``keyword_match`` (``docs/architecture.md`` section 3), which is how the content manager
tunes the lists against real runs; they are not debug output.
"""

from __future__ import annotations

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
    "DEFAULT_REVIEW_BAND",
    "DEFAULT_SNIPPET_RADIUS",
    "SCHEMA_FILENAME",
    "Exclusion",
    "KeywordFilter",
    "KeywordList",
    "KeywordListError",
    "KeywordListNotFoundError",
    "KeywordListValidationError",
    "KeywordTerm",
    "fold_diacritics",
    "load_keyword_list",
    "load_keyword_list_for",
]

logger = logging.getLogger(__name__)

#: Name of the JSON Schema file inside the configured keywords directory.
SCHEMA_FILENAME: Final[str] = "schema.json"

#: Characters of context kept either side of a match in a reported snippet.
DEFAULT_SNIPPET_RADIUS: Final[int] = 90

#: Width of the review band used by a list that declares none, in score points above
#: ``min_score``. Three points is one weight-3 term at the full-text multiplier: a document
#: below it was admitted on a single unambiguous mention or on an accumulation of contextual
#: ones, which is exactly the population the dry runs found the false positives in
#: (``docs/core-document.md`` section 2.7). A new jurisdiction inherits it until its own dry
#: run gives it a number, and inheriting a *wide* band is the recall-safe direction: it
#: over-reviews rather than under-reviews.
DEFAULT_REVIEW_BAND: Final[float] = 3.0

#: Tolerance on the ``score >= min_score`` comparison, so a total assembled from float
#: multipliers is never rejected by a representation error of the order of 1e-16.
_SCORE_EPSILON: Final[float] = 1e-9

#: Number of contributing term ids named in a result's reason before it is abbreviated.
_REASON_TERM_LIMIT: Final[int] = 8

#: Number of schema violations reported before the message is abbreviated.
_ERROR_LIMIT: Final[int] = 10

#: The ``match`` modes ``schema.json`` allows.
MatchMode = Literal["word", "phrase", "substring", "regex"]

_MATCH_MODES: Final[frozenset[str]] = frozenset({"word", "phrase", "substring", "regex"})

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


class KeywordListError(Exception):
    """Base class for every failure to load or use a keyword list."""


class KeywordListNotFoundError(KeywordListError):
    """A jurisdiction has no keyword list file.

    A jurisdiction cannot be onboarded before its list exists (``docs/core-document.md``
    section 2.5), so this is an onboarding error, not a reason to fall back to matching
    nothing.
    """


class KeywordListValidationError(KeywordListError):
    """A keyword list is malformed, invalid against the schema or internally inconsistent."""


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
    offsets and snippets are taken from the unfolded text, so a match on ``neonicotinoide``
    still reports the document's own ``neonicotinoïde``.

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
class KeywordTerm:
    """One curated term, with its aliases and its gate.

    Attributes:
        term_id: Stable id, e.g. ``nl-drift``. Match provenance is stored against it.
        term: The term as it appears in court documents, in the source language.
        lang: ISO 639-1 code of the language the term is written in.
        category: Curation category, used for reporting on what drives recall.
        weight: 3 qualifies a document alone, 2 is strong, 1 is contextual only.
        match: How the term is matched: ``word``, ``phrase``, ``substring`` or ``regex``.
        case_sensitive: Whether casing must match, for acronyms whose lowercase form is a
            common word.
        aliases: Spelling variants and inflections that score identically and report
            :attr:`term_id`.
        requires: Term ids that must also match before this term contributes any weight.
        note: The curator's rationale, if recorded.
    """

    term_id: str
    term: str
    lang: str
    category: str
    weight: float
    match: MatchMode
    case_sensitive: bool
    aliases: tuple[str, ...]
    requires: tuple[str, ...]
    note: str | None

    @property
    def patterns(self) -> tuple[str, ...]:
        """Return the term and its aliases, which all score identically."""
        return (self.term, *self.aliases)


@dataclass(frozen=True, slots=True)
class Exclusion:
    """A pattern that vetoes a document outright, regardless of its score.

    Attributes:
        pattern: The pattern text.
        match: How it is matched; the schema defaults it to ``phrase``.
        reason: Why the trap exists, reported on the rejection.
    """

    pattern: str
    match: MatchMode
    reason: str


@dataclass(frozen=True, slots=True)
class _PatternSpec:
    """One literal to compile, and who to credit when it matches.

    Attributes:
        owner: Key credited on a match: a term id, or an exclusion's index.
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
        owners: Normalised literal to the ids of every term that declared it. Terms written
            identically in different languages - ``pesticide`` in English, French and Dutch
            in the EU list - share one literal and are all credited by it.
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

    def first(self, cased: str, folded: str) -> tuple[str, int, int] | None:
        """Return the first occurrence found, without scanning any further.

        Args:
            cased: Diacritic-folded text with its casing intact.
            folded: The same text, case-folded as well.

        Returns:
            The owner key and the offsets of one occurrence, or ``None``.
        """
        for owners, start, end in self.scan(cased, folded):
            return owners[0], start, end
        return None


@dataclass(slots=True)
class _Hit:
    """Accumulated occurrences of one term in one field.

    Counted rather than collected: a term repeated a thousand times in a full text must cost
    a counter, not a thousand objects.

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
class KeywordList:
    """A jurisdiction's curated keyword list, loaded, validated and compiled.

    Building one is the expensive part - reading, schema validation, pattern compilation -
    and happens once per list per process, never per document.

    Attributes:
        jurisdiction: Jurisdiction code, ``NL`` or ``EU``.
        jurisdiction_name: Human-readable jurisdiction name.
        list_version: Semantic version of the list, stored with every match.
        updated: ISO 8601 date the list was last curated.
        languages: ISO 639-1 codes the list covers.
        min_score: Total weight a document must reach to pass.
        review_band: Width of the band immediately above :attr:`min_score` within which a
            passing document is additionally flagged for review. Per list, because the
            evidence that sets it is per corpus; ``0`` disables flagging.
        count_term_once: Whether repeated occurrences of a term count only once.
        field_multipliers: Per-field weight multipliers, in curation order. A field absent
            from this mapping is not scanned at all: the curator decides which fields count.
        terms: The terms, keyed by id, in file order.
        exclusions: Patterns that veto a document regardless of score.
        source_path: File the list was read from.
        patterns: Compiled term patterns.
        exclusion_patterns: Compiled exclusion patterns, owned by index into
            :attr:`exclusions`.
    """

    jurisdiction: str
    jurisdiction_name: str
    list_version: str
    updated: str
    languages: tuple[str, ...]
    min_score: float
    review_band: float
    count_term_once: bool
    field_multipliers: Mapping[str, float]
    terms: Mapping[str, KeywordTerm]
    exclusions: tuple[Exclusion, ...]
    source_path: Path
    patterns: _PatternSet
    exclusion_patterns: _PatternSet

    @property
    def term_count(self) -> int:
        """Return the number of curated terms, aliases excluded."""
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
    def review_ceiling(self) -> float:
        """Return the score at or above which a passing document is *not* flagged.

        Returns:
            ``min_score + review_band``. The band is the half-open interval
            ``[min_score, review_ceiling)``, so a document exactly on the ceiling passes
            confidently and one exactly on ``min_score`` is flagged.
        """
        return self.min_score + self.review_band

    def in_review_band(self, score: float) -> bool:
        """Return whether a score falls in the review band.

        Deterministic given the list version and the score, which is what makes a flag
        reproducible from the recorded run (``docs/core-document.md`` section 2.8). The
        epsilon is the one used for ``min_score`` and errs towards review: a total whose
        float representation lands a fraction under the ceiling is flagged rather than
        waved through.

        Args:
            score: Total weight the document accumulated.

        Returns:
            ``True`` when ``min_score <= score < review_ceiling``.
        """
        if self.review_band <= 0:
            return False
        return (
            score + _SCORE_EPSILON >= self.min_score
            and score + _SCORE_EPSILON < self.review_ceiling
        )

    def find_exclusion(self, cased: str, folded: str) -> tuple[Exclusion, int, int] | None:
        """Return the first exclusion vetoing the text, if any.

        Args:
            cased: Diacritic-folded text with its casing intact.
            folded: The same text, case-folded as well.

        Returns:
            The exclusion and the offsets it matched at, or ``None``.
        """
        found = self.exclusion_patterns.first(cased, folded)
        if found is None:
            return None
        owner, start, end = found
        return self.exclusions[int(owner)], start, end


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
    instead of retrying every term in turn. A space in a literal becomes ``\s+``: a phrase
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
    a leading bracket would be wrong. ``substring`` deliberately needs none, which is how a
    compounding language finds ``gewasbeschermingsmiddel`` inside
    ``gewasbeschermingsmiddelenrichtlijn``.

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


def _build_pattern_set(specs: Sequence[_PatternSpec], *, described_as: str) -> _PatternSet:
    """Compile every literal of one list into its trie buckets and its regex patterns.

    Args:
        specs: The literals to compile, with their owners and modes.
        described_as: Noun used in error messages, ``term`` or ``exclusion``.

    Returns:
        The compiled pattern set.

    Raises:
        KeywordListValidationError: If a literal is blank or an expression does not compile.
    """
    grouped: dict[_BucketKey, dict[str, list[str]]] = {}
    regexes: list[tuple[str, re.Pattern[str]]] = []
    for spec in specs:
        owner_label = f"{described_as} {spec.owner!r}"
        if spec.mode == "regex":
            regexes.append((spec.owner, _compile_regex(spec, owner=owner_label)))
            continue
        literal = _collapse_whitespace(
            fold_diacritics(_normalise(spec.literal), lower=not spec.case_sensitive)
        )
        if not literal:
            message = f"{owner_label}: pattern {spec.literal!r} is empty once stripped"
            raise KeywordListValidationError(message)
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
        KeywordListValidationError: If the expression does not compile.
    """
    flags = re.UNICODE if spec.case_sensitive else re.UNICODE | re.IGNORECASE
    try:
        return re.compile(spec.literal, flags)
    except re.error as error:
        message = f"{owner}: invalid regular expression {spec.literal!r}: {error}"
        raise KeywordListValidationError(message) from error


def _term_from_raw(raw: Mapping[str, Any]) -> KeywordTerm:
    """Build a typed term from its schema-validated JSON object.

    Args:
        raw: One entry of the list's ``terms`` array.

    Returns:
        The typed term.

    Raises:
        KeywordListValidationError: If the match mode is not one the matcher implements.
    """
    match_mode = str(raw.get("match", "word"))
    if match_mode not in _MATCH_MODES:  # pragma: no cover - the schema rejects this first
        message = f"term {raw.get('id')!r}: unknown match mode {match_mode!r}"
        raise KeywordListValidationError(message)
    note = raw.get("note")
    return KeywordTerm(
        term_id=str(raw["id"]),
        term=str(raw["term"]),
        lang=str(raw["lang"]),
        category=str(raw["category"]),
        weight=float(raw["weight"]),
        match=cast(MatchMode, match_mode),
        case_sensitive=bool(raw.get("case_sensitive", False)),
        aliases=tuple(str(alias) for alias in raw.get("aliases", ())),
        requires=tuple(str(required) for required in raw.get("requires", ())),
        note=str(note) if note is not None else None,
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
        KeywordListError: If the schema file is missing or is not valid JSON.
        KeywordListValidationError: If the document violates the schema.
    """
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        message = f"keyword list schema not found at {schema_path}"
        raise KeywordListError(message) from error
    except json.JSONDecodeError as error:
        message = f"keyword list schema at {schema_path} is not valid JSON: {error}"
        raise KeywordListError(message) from error

    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path))
    if not errors:
        return
    detail = "; ".join(_describe(error, document) for error in errors[:_ERROR_LIMIT])
    if len(errors) > _ERROR_LIMIT:
        detail = f"{detail}; and {len(errors) - _ERROR_LIMIT} further violation(s)"
    message = f"invalid keyword list: {detail}"
    raise KeywordListValidationError(message)


def _check_consistency(terms: Sequence[KeywordTerm]) -> None:
    """Check what the schema cannot: duplicate ids and gates that can never open.

    Args:
        terms: The terms of one list, in file order.

    Raises:
        KeywordListValidationError: If an id repeats, or a ``requires`` entry names a term
            that is not in the list or names the term itself.
    """
    seen: set[str] = set()
    for term in terms:
        if term.term_id in seen:
            message = f"term {term.term_id!r}: duplicate term id"
            raise KeywordListValidationError(message)
        seen.add(term.term_id)
    for term in terms:
        for required in term.requires:
            if required == term.term_id:
                message = f"term {term.term_id!r}: requires itself"
                raise KeywordListValidationError(message)
            if required not in seen:
                message = (
                    f"term {term.term_id!r}: requires unknown term id {required!r}; "
                    "a gate that can never open would silence the term"
                )
                raise KeywordListValidationError(message)


def _read_list(path: Path) -> object:
    """Read and parse one list file.

    Args:
        path: Path to the list file.

    Returns:
        The parsed JSON document.

    Raises:
        KeywordListNotFoundError: If the file does not exist.
        KeywordListValidationError: If the file is not valid JSON.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        message = (
            f"no keyword list at {path}; a jurisdiction cannot be ingested before its list "
            "exists (core document section 2.5)"
        )
        raise KeywordListNotFoundError(message) from error
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        message = f"keyword list at {path} is not valid JSON: {error}"
        raise KeywordListValidationError(message) from error


def load_keyword_list(path: Path, *, schema_path: Path | None = None) -> KeywordList:
    """Read, validate and compile one jurisdiction's keyword list.

    Validation is strict and loud by design: a list that violates
    ``data/keywords/schema.json``, repeats an id or gates a term behind an id that does not
    exist raises an error naming the offending term, rather than silently matching less.

    Args:
        path: Path to the list file.
        schema_path: Path to the schema. Defaults to ``schema.json`` beside the list.

    Returns:
        The compiled list, ready to match documents.

    Raises:
        KeywordListNotFoundError: If the list file does not exist.
        KeywordListValidationError: If the file is not valid JSON, violates the schema or
            breaks a cross-term rule.
        KeywordListError: If the schema file itself cannot be read.
    """
    document = _read_list(path)
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

    exclusions = tuple(
        Exclusion(
            pattern=str(entry["pattern"]),
            match=cast(MatchMode, str(entry.get("match", "phrase"))),
            reason=str(entry["reason"]),
        )
        for entry in raw.get("exclusions", ())
    )
    exclusion_specs = [
        _PatternSpec(
            owner=str(index),
            literal=exclusion.pattern,
            mode=exclusion.match,
            case_sensitive=False,
        )
        for index, exclusion in enumerate(exclusions)
    ]

    scoring = cast(Mapping[str, Any], raw["scoring"])
    keyword_list = KeywordList(
        jurisdiction=str(raw["jurisdiction"]),
        jurisdiction_name=str(raw["jurisdiction_name"]),
        list_version=str(raw["list_version"]),
        updated=str(raw["updated"]),
        languages=tuple(str(code) for code in raw["languages"]),
        min_score=float(scoring["min_score"]),
        review_band=float(scoring.get("review_band", DEFAULT_REVIEW_BAND)),
        count_term_once=bool(scoring.get("count_term_once", True)),
        field_multipliers={str(name): float(value) for name, value in scoring["fields"].items()},
        terms={term.term_id: term for term in terms},
        exclusions=exclusions,
        source_path=path,
        patterns=_build_pattern_set(term_specs, described_as="term"),
        exclusion_patterns=_build_pattern_set(exclusion_specs, described_as="exclusion"),
    )
    logger.info(
        "Loaded keyword list %s v%s from %s: %d terms, %d literals, %d scans per document; "
        "min_score %g, review band [%g, %g)",
        keyword_list.jurisdiction,
        keyword_list.list_version,
        path.name,
        keyword_list.term_count,
        keyword_list.pattern_count,
        keyword_list.scan_count,
        keyword_list.min_score,
        keyword_list.min_score,
        keyword_list.review_ceiling,
    )
    return keyword_list


@lru_cache(maxsize=32)
def _load_cached(path: Path, schema_path: Path, fingerprint: tuple[int, int]) -> KeywordList:
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
    return load_keyword_list(path, schema_path=schema_path)


def load_keyword_list_for(jurisdiction_code: str, settings: Settings | None = None) -> KeywordList:
    """Load a jurisdiction's keyword list, compiling it at most once per process.

    Args:
        jurisdiction_code: Jurisdiction code such as ``NL`` or ``EU``, case-insensitive.
        settings: Settings resolving the keywords directory. Defaults to the process-wide
            settings.

    Returns:
        The compiled list for that jurisdiction.

    Raises:
        KeywordListNotFoundError: If the jurisdiction has no list file.
        KeywordListValidationError: If the list is invalid.
    """
    resolved = settings if settings is not None else get_settings()
    path = resolved.keyword_list_path(jurisdiction_code)
    try:
        stat = path.stat()
    except OSError as error:
        message = (
            f"no keyword list for jurisdiction {jurisdiction_code!r} at {path}; a "
            "jurisdiction cannot be ingested before its list exists (core document 2.5)"
        )
        raise KeywordListNotFoundError(message) from error
    schema_path = resolved.keywords_dir / SCHEMA_FILENAME
    return _load_cached(path, schema_path, (stat.st_size, stat.st_mtime_ns))


class KeywordFilter(Filter):
    """Filter stage 1: score a document against one jurisdiction's curated list.

    The list is compiled when the stage is constructed, so evaluating a document costs a
    handful of scans of its text whatever the size of the list - see
    :attr:`KeywordList.scan_count`.

    Attributes:
        name: Stage name reported on every result.
    """

    name = "keywords"

    def __init__(
        self,
        keyword_list: KeywordList,
        *,
        snippet_radius: int = DEFAULT_SNIPPET_RADIUS,
    ) -> None:
        """Bind the stage to a compiled list.

        Args:
            keyword_list: The compiled list to match against.
            snippet_radius: Characters of context kept either side of a reported match.

        Raises:
            ValueError: If the snippet radius is negative.
        """
        if snippet_radius < 0:
            message = f"snippet_radius must not be negative, got {snippet_radius}"
            raise ValueError(message)
        self._list = keyword_list
        self._snippet_radius = snippet_radius

    @classmethod
    def for_jurisdiction(
        cls,
        jurisdiction_code: str,
        *,
        settings: Settings | None = None,
        snippet_radius: int = DEFAULT_SNIPPET_RADIUS,
    ) -> KeywordFilter:
        """Build the stage for a jurisdiction, loading its list through the settings.

        Args:
            jurisdiction_code: Jurisdiction code such as ``NL`` or ``EU``.
            settings: Settings resolving the keywords directory.
            snippet_radius: Characters of context kept either side of a reported match.

        Returns:
            A stage bound to that jurisdiction's list.

        Raises:
            KeywordListNotFoundError: If the jurisdiction has no list file.
        """
        return cls(
            load_keyword_list_for(jurisdiction_code, settings),
            snippet_radius=snippet_radius,
        )

    @property
    def keyword_list(self) -> KeywordList:
        """Return the compiled list this stage matches against."""
        return self._list

    def evaluate(self, case: FilterableDocument) -> FilterResult:
        """Score one document against the list and decide whether it is in scope.

        Only the fields named in the list's ``scoring.fields`` are read, and a field the
        document does not carry is treated as absent, so a source without an abstract needs
        no adapter. Fields are scanned one at a time and only counters are kept, so peak
        memory stays proportional to the largest field rather than to the number of matches.

        Args:
            case: The normalised document to judge.

        Returns:
            The verdict, carrying the score, every term match found and a readable reason.
        """
        hits: dict[str, dict[str, _Hit]] = {}
        for field_name in self._list.field_multipliers:
            text = getattr(case, field_name, None)
            if not isinstance(text, str) or not text:
                continue
            reference = _normalise(text)
            cased = fold_diacritics(reference)
            folded = fold_diacritics(reference, lower=True)

            vetoed = self._list.find_exclusion(cased, folded)
            if vetoed is not None:
                return self._veto(vetoed, reference, field_name)
            self._collect(cased, folded, reference, field_name, hits)

        return self._score(hits)

    def _veto(
        self,
        vetoed: tuple[Exclusion, int, int],
        reference: str,
        field_name: str,
    ) -> FilterResult:
        """Build the result for a document an exclusion rejected outright.

        Args:
            vetoed: The exclusion and the offsets it matched at.
            reference: The field text the offsets refer to.
            field_name: Name of the field the exclusion matched in.

        Returns:
            A failing result explaining which trap fired and why it exists.
        """
        exclusion, start, end = vetoed
        reason = (
            f"vetoed by exclusion {exclusion.pattern!r} in {field_name} "
            f"(matched {reference[start:end]!r}): {exclusion.reason}"
        )
        logger.debug("Keyword filter vetoed a document: %s", reason)
        return FilterResult(passed=False, score=0.0, reason=reason, stage=self.name, matches=())

    def _collect(
        self,
        cased: str,
        folded: str,
        reference: str,
        field_name: str,
        hits: dict[str, dict[str, _Hit]],
    ) -> None:
        """Accumulate every term occurrence found in one field.

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

    def _score(self, hits: Mapping[str, Mapping[str, _Hit]]) -> FilterResult:
        """Apply gates, multipliers and ``count_term_once``, then decide.

        Args:
            hits: Occurrences per term id and field, as collected from the document.

        Returns:
            The verdict. A gated term is reported with a weight of ``0.0``, so the content
            manager can see that a homonym was disarmed rather than missed.
        """
        matched_ids = set(hits)
        matches: list[TermMatch] = []
        contributors: list[str] = []
        score = 0.0

        for term_id, per_field in hits.items():
            term = self._list.terms[term_id]
            gated = any(required not in matched_ids for required in term.requires)
            weights = self._weights(term, per_field, gated=gated)
            for field_name, hit in per_field.items():
                applied = weights[field_name]
                score += applied
                matches.append(
                    TermMatch(
                        term_id=term_id,
                        list_version=self._list.list_version,
                        field=field_name,
                        weight_applied=applied,
                        snippet=hit.snippet,
                        matched_text=hit.matched_text,
                        occurrences=hit.occurrences,
                        start=hit.start,
                        end=hit.end,
                        gated=gated,
                    )
                )
            if not gated and term.weight > 0:
                contributors.append(term_id)

        passed = score + _SCORE_EPSILON >= self._list.min_score
        needs_review = passed and self._list.in_review_band(score)
        return FilterResult(
            passed=passed,
            score=score,
            reason=self._reason(score, contributors, passed=passed, needs_review=needs_review),
            stage=self.name,
            matches=tuple(matches),
            needs_review=needs_review,
            threshold=self._list.min_score,
            review_ceiling=self._list.review_ceiling,
        )

    def _weights(
        self,
        term: KeywordTerm,
        per_field: Mapping[str, _Hit],
        *,
        gated: bool,
    ) -> Mapping[str, float]:
        """Compute what each field's occurrences of one term contribute.

        Under ``count_term_once`` the term contributes once for the whole document, credited
        to the field with the highest multiplier so a title hit is not lost behind a
        full-text hit; the other fields are reported at zero. Otherwise every occurrence
        counts.

        Args:
            term: The term being scored.
            per_field: Its occurrences per field.
            gated: Whether its ``requires`` gate stayed shut.

        Returns:
            Weight per field name, summing to the term's contribution to the score.
        """
        multipliers = self._list.field_multipliers
        if gated:
            return dict.fromkeys(per_field, 0.0)
        if self._list.count_term_once:
            best = max(per_field, key=lambda name: multipliers.get(name, 0.0))
            weights = dict.fromkeys(per_field, 0.0)
            weights[best] = term.weight * multipliers.get(best, 0.0)
            return weights
        return {
            name: term.weight * multipliers.get(name, 0.0) * hit.occurrences
            for name, hit in per_field.items()
        }

    def _reason(
        self,
        score: float,
        contributors: Sequence[str],
        *,
        passed: bool,
        needs_review: bool,
    ) -> str:
        """Phrase the verdict for the pipeline report.

        Args:
            score: Total weight accumulated.
            contributors: Ids of the terms that contributed weight.
            passed: Whether the document reached ``min_score``.
            needs_review: Whether the score fell in the review band.

        Returns:
            A one-line explanation naming the list, the score, the driving terms and, for a
            flagged document, the band it fell in. The band is stated rather than implied so
            the sentence stands on its own in a match report and on a methodology page.
        """
        provenance = f"{self._list.jurisdiction} list v{self._list.list_version}"
        if not contributors:
            return f"no curated term contributed weight ({provenance})"
        listing = ", ".join(contributors[:_REASON_TERM_LIMIT])
        if len(contributors) > _REASON_TERM_LIMIT:
            listing = f"{listing} and {len(contributors) - _REASON_TERM_LIMIT} more"
        comparison = "reaches" if passed else "is below"
        band = (
            f", inside the review band [{self._list.min_score:g}, {self._list.review_ceiling:g})"
            if needs_review
            else ""
        )
        return (
            f"score {score:g} {comparison} min_score {self._list.min_score:g}{band} "
            f"({provenance}); matched: {listing}"
        )
