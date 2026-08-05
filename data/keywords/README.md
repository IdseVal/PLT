# Keyword filter lists

Stage 1 of the PLT ingestion filter chain. See
[`docs/core-document.md` §2.5](../../docs/core-document.md#25-linguistic-filtering-and-per-jurisdiction-keyword-lists)
for why this exists.

## Files

| File | Purpose |
| --- | --- |
| `schema.json` | JSON Schema (2020-12) every list validates against. |
| `nl.json` | Netherlands — Dutch. |
| `eu.json` | European Union — English, French, German, Dutch. |

**One file per jurisdiction, named `<jurisdiction-code-lowercase>.json`.** A jurisdiction
cannot be onboarded to the pipeline until its list exists: the terms are language-,
legal-system- and agronomy-specific and do not transfer between countries.

## Adding a jurisdiction

1. Copy the closest existing list as a starting point for structure only — **not** for terms.
2. Set `jurisdiction`, `jurisdiction_name`, `languages`, and reset `list_version` to `1.0.0`.
3. Write terms in the working language(s) of that jurisdiction's courts. Cover at minimum:
   - product classes (pesticide, plant protection product, biocide, herbicide, …)
   - active substances and well-known brands litigated in that country
   - the national statute(s), the authorising body, and the authorisation procedure
   - crops and practices that generate local litigation
   - exposure, residue and environmental terms
4. Validate against `schema.json` before committing.
5. Run the pipeline in dry-run mode over a sample period and review the match report before
   enabling ingestion.

## Weighting

| Weight | Meaning |
| --- | --- |
| **3** | Unambiguous. Reaching `scoring.min_score` on this term alone is intended. |
| **2** | Strong, but wants light corroboration. |
| **1** | Contextual only. Meaningless in isolation — `lelieteelt`, `drift`, `omwonenden`, `REACH`. |

A document passes when its total weight (after per-field multipliers) reaches
`scoring.min_score`. `requires` disarms homonyms by making a term score nothing unless
another term also matched — `nl-drift` is the worked example, since *drift* also means
*fit of anger* in Dutch criminal judgments.

## The review band

`scoring.review_band` is the width, in score points **above `min_score`**, of the band in
which a passing document is additionally flagged for a content manager. A document scoring
`min_score ≤ score < min_score + review_band` is ingested and published exactly like any
other and appears in the review queue (`GET /api/reviews`). A band of `0` disables flagging;
a list that omits the key inherits **3**.

This exists because the PLT optimises for recall
([core document §2.7](../../docs/core-document.md)). `min_score` is deliberately not raised
to buy precision — a false negative is a case the tracker implicitly claims does not exist —
so precision is bought downstream instead: **selection admits, review curates.** Flagging
never rejects, and never withholds a case from the site.

**Set the band from your own jurisdiction's dry run, not from another list's.** The shipped
values were each derived from their own corpus, and they differ:

| List | `min_score` | `review_band` | Flagged range | Evidence |
| --- | ---: | ---: | --- | --- |
| `eu.json` | 3 | 3 | `[3, 6)` | First EU dry run: 1,548 CJEU decisions across 2024, 54 passed. False positives concentrated at 3.0–3.9; the ≥12 band almost entirely genuine. Raising `min_score` to 6 would have removed 21 cases, ~2 of them in scope — declined, so that population is reviewed instead. |
| `nl.json` | 3 | 2.5 | `[3, 5.5)` | First Rechtspraak dry run: 10,011 documents, 38 passed. Every clear false positive scored at or below 5.0 and none above, so the band covers them with a margin. |

Practical notes for a curator:

- **The interval is half-open.** A score exactly on `min_score` is flagged; one exactly on
  the ceiling is not. Widen the band rather than nudging a score if a case on the boundary
  should be reviewed.
- **Widening is cheap, narrowing is not.** A band that is too wide costs a reviewer some
  minutes; one that is too narrow lets the borderline false positives through unexamined,
  which is the failure mode this mechanism exists to catch.
- **Bump `list_version` when you change the band.** The flag is stored with the version that
  produced it, and re-running a window against the same version must reproduce it exactly
  ([core document §2.8](../../docs/core-document.md)).
- **A dry run shows the effect without writing a row.** Every line of the match report
  carries `needs_review`, `threshold` and `review_ceiling`.

## Case sensitivity

**`case_sensitive` applies to a term *and every one of its aliases*.** The schema cannot
express it per alias, so the rule for every list is:

> A `case_sensitive: true` term carries **acronyms only**. Never mix an acronym and an
> ordinary word in the same term.

An ordinary word inheriting the flag silently loses every sentence-initial occurrence — a
judgment opening "Lindaan is in de bodem aangetroffen" would not have matched, while the
same word mid-sentence would. The split terms `nl-ddt` / `nl-organochloor` and
`nl-ctgb` / `nl-college-toelating` exist for exactly this reason; follow that pattern.

Only set `case_sensitive` where the lowercase form is a common word or would over-match
(`Ctgb`, `DDT`, `REACH`, `NVWA`). Everything else stays case-insensitive.

### Known exceptions

Four authority terms keep a spelled-out name as an alias and are therefore **not** compliant
with the rule above: `nl-nvwa-gewas`, `nl-efsa`, `en-efsa`, `en-echa`.

They are exceptions on purpose, because splitting them would change scoring rather than just
structure. `count_term_once` counts per **term**, so splitting a weight-1 term in two either
doubles the score of a document that names the authority both ways, or halves the score of
one that names it once, depending on how the halves are weighted. Neither is acceptable for
a contextual term sitting one point below the threshold, and there is no weighting that is
right in both shapes.

**The status quo is not cost-free**, and whoever decides #24 should know what it costs.

The failure mode is **not** lower-casing. Under `case_sensitive: true` a spelled-out alias
matches **only the exact string as curated** — every other casing scores zero, including
renderings a court is likely to produce:

| Text | `nl-efsa` |
| --- | ---: |
| `Europese Autoriteit voor voedselveiligheid` (as curated) | 1.00 |
| `Europese Autoriteit voor Voedselveiligheid` (capital V) | **0.00** |
| `europese autoriteit voor voedselveiligheid` | **0.00** |
| `EUROPESE AUTORITEIT VOOR VOEDSELVEILIGHEID` (heading) | **0.00** |

The same holds for all four terms: `Nederlandse voedsel- en Warenautoriteit`,
`European food safety authority` and `European chemicals agency` all score 0.00 today.

What limits the damage is **not** that these names are always capitalised — they are not.
Nor is it that the acronym is case-robust, which an earlier version of this section claimed.
**The acronym is case-sensitive too**, so `efsa`, `Efsa`, `nvwa` and `echa` all score 0.00,
and nothing else in either list catches them. Under `case_sensitive`, *every* form of these
terms matches only the exact curated casing.

What bounds the cost is the **weight** — but the bound is larger than the weight column
suggests, because `scoring.fields` multiplies it. A weight-1 term contributes:

| Field it matched in | Contribution |
| --- | ---: |
| `full_text` (×1.0) | 1.00 |
| `subject` (×1.0 in `nl.json`, ×1.2 in `eu.json`) | 1.00 / 1.20 |
| `abstract` (×1.5) | **1.50** |
| `title` (×1.5) | **1.50** |

A term matching in several fields does **not** accumulate multipliers. Under
`count_term_once` the matcher credits the term to its single highest-multiplier field and
zeroes the rest, so one term contributes `weight × max(multiplier)` however many fields it
hits. That is what makes 1.50 a true ceiling rather than the largest case anyone happened to
try: across all four terms and every combination of fields, only three contributions are
reachable — 1.00, 1.20 and 1.50.

So a miss costs up to **1.50** against a `min_score` of 3, and the vulnerable band is any
document already scoring in **`[1.50, 3.00)`** from other terms — not, as this section
previously said, "already sitting at 2.x". Worked counterexample from the shipped `eu.json`:

| Document | Score | Verdict |
| --- | ---: | --- |
| `Aarhus information request` in the abstract | 1.50 | rejected |
| …plus `European Chemicals Agency` in the title | 3.00 | **accepted** |
| …plus `European chemicals agency` (two letters lower-cased) | 1.50 | rejected |

Two characters of casing decide whether that case enters the database.

Bounded, then, but across a wider band than the raw weights imply, and the band is exactly
where these contextual terms exist to tip the balance. **Treat all four as equally brittle**:
there is no safe one among them, and no casing of any of them is safe except the one curated
string.

**Gating on a pesticide term — the proposal in #24 — does not fix any of this.** The two
problems are independent. If these terms are restructured anyway, dropping `case_sensitive`
from the spelled-out halves closes the casing gap at the same time.

Resolving this properly needs a curation decision (issue #24) about whether contextual
authority terms should be *gated* on a pesticide term rather than merely weighted. Note that
`requires` cannot currently express that: it is an **AND** over term ids, so there is no way
to say "requires any pesticide term".

Do not split these four without reading #24 first.

## Curation

These lists are **data curated by the content manager**, not code. Every ingestion run
records which term ids matched each case, so precision and recall can be reviewed and the
lists tuned. Bump `list_version` on every change and note the reasoning in `notes`.
