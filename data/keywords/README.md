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

## Curation

These lists are **data curated by the content manager**, not code. Every ingestion run
records which term ids matched each case, so precision and recall can be reviewed and the
lists tuned. Bump `list_version` on every change and note the reasoning in `notes`.
