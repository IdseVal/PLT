"""Find keyword literals that match inside words they have nothing to do with.

A `substring` keyword matches anywhere, which is what lets `biocide` catch
`biocidenverordening`. The same rule lets `aldrin` catch `Aldringer`, a street in Luxembourg,
and select 41 judgments about coal and steel. No rule about length separates those two cases.
Only reading the corpus does.

This reads it. It builds the vocabulary of a sample of stored cases, then reports every
substring literal that turns up inside a longer word, with how often. Judging the result is a
person's job: `biociden` inside `biocidenrichtlijn` is the feature, `aldrin` inside
`Aldringer` is the bug, and they look identical to a program.

Run it before giving any short literal `substring`, and after adding a batch of substances:

    .venv/Scripts/python.exe scripts/substring_traps.py NL ../data/keywords/nl.json 30000

It reads the local corpus store and sends no requests.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from plt.pipeline.store_source import stored_corpus_connector

#: Letters only: digits and punctuation are not part of the words a keyword hides inside.
WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def vocabulary(jurisdiction: str, sample: int) -> Counter[str]:
    """Count the words used across a sample of one jurisdiction's stored cases.

    Args:
        jurisdiction: Jurisdiction code, e.g. ``NL``.
        sample: How many cases to read.

    Returns:
        Each lower-cased word form and how often it occurs.
    """
    connector = stored_corpus_connector(jurisdiction)
    words: Counter[str] = Counter()
    read = 0
    try:
        for candidate in connector.discover(None, None):
            try:
                case = connector.normalise(connector.fetch(candidate))
            except Exception as error:  # a hole in the corpus is not what this is measuring
                print(f"  skipped {candidate.source_id}: {error}", file=sys.stderr)
                continue
            for document in case.documents:
                if document.full_text:
                    words.update(word.lower() for word in WORD.findall(document.full_text))
            read += 1
            if read >= sample:
                break
    finally:
        connector.close()
    print(f"read {read} cases, {len(words)} distinct words", file=sys.stderr)
    return words


def substring_literals(path: Path) -> list[tuple[str, str]]:
    """Return every ``(term id, literal)`` a keyword list matches as a substring.

    Args:
        path: The keyword list to read.

    Returns:
        One pair per literal, aliases included, since an alias inherits the match mode.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        (term["id"], text.lower())
        for term in data["terms"]
        if term.get("match", "word") == "substring"
        for text in (term["term"], *term.get("aliases", []))
        if " " not in text
    ]


def main() -> None:
    """Report the containing words for every substring literal in a list."""
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    jurisdiction, list_path = sys.argv[1], Path(sys.argv[2])
    sample = int(sys.argv[3]) if len(sys.argv) > 3 else 20000

    words = vocabulary(jurisdiction, sample)
    print(f"\n=== {jurisdiction}: substring literals found inside longer words ===")
    for term_id, literal in substring_literals(list_path):
        hosts = {word: n for word, n in words.items() if literal in word and word != literal}
        if not hosts:
            continue
        ranked = sorted(hosts.items(), key=lambda pair: -pair[1])
        print(f"\n  {term_id}  literal={literal!r}")
        print(f"    in {len(hosts)} words, {sum(hosts.values())} uses")
        for word, count in ranked[:6]:
            print(f"      {count:>7}  {word}")


if __name__ == "__main__":
    main()
