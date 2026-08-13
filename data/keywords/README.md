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

## Active substances: where the list comes from

**Every jurisdiction's list carries the active substances authorised in that jurisdiction,
enumerated from a register rather than written from memory.** A named active substance is
the least ambiguous signal this filter has, and the set is large enough — hundreds of names
per jurisdiction — that hand-picking the famous ones is how a list quietly acquires a recall
floor.

| Level | Source | Notes |
| --- | --- | --- |
| EU | The Annex to **Commission Implementing Regulation (EU) No 540/2011** (CELEX `32011R0540`), consolidated text on EUR-Lex at `https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02011R0540-<date>`. | Parts A–E: approved, basic, low-risk, candidates for substitution. |
| National | The **national authorisation register**. For the Netherlands that is the Ctgb register at `https://toelatingen.ctgb.nl`; for France it is the ANSES *E-Phy* register, for Germany the BVL *Pflanzenschutzmittel-Verzeichnis*. | Take the register's own substance list, not a product list. |

Four rules that are easy to get wrong:

- **Include substances that are no longer approved or authorised.** Historic exposure and
  liability litigation is largely *about* withdrawn substances, and a register shows you
  today. The consolidated Annex to 540/2011 deletes a substance when its approval is not
  renewed, so read **every** consolidated version, not only the current one, and take the
  union; a national register that keeps expired authorisations gives you this for free.
- **Take the national spelling as well as the international one.** Dutch judgments write
  both `chloorpyrifos` and `chlorpyrifos`, both `glyfosaat` and `glyphosate`. Carry the
  national name as the term and the other spelling as an **alias**, so one occurrence scores
  once. Never give the same literal to two terms — the score would count it twice.
- **Weight 3, category `active_substance`.** A named substance qualifies a document alone,
  which is the point.
- **Check the short and word-like names before you commit them.** See below.

### Substance names that are also ordinary words

A register contains `beer`, `vinegar`, `sucrose`, `urea`, `talc`, `water`, `koper` — which in
Dutch is also a *buyer* — and `jood`, which is also a *Jew*. At weight 3 each of those would
admit and publish any judgment that happens to contain the word.

Two mechanisms, used together:

1. **`match`.** Multi-word names are `phrase`. A single-token name is `word` — a word
   boundary still lets `diquat` match inside `diquat-dibromide`, because a hyphen is not a
   word character — unless the name is at least ten characters long, where `substring` is
   safe and also catches the compounds Dutch and German form. Never `substring` on a short
   name: that is how `kwekerij` came to match `hennepkwekerij`.

   **The loader enforces a floor of six characters on every `substring` literal, term and
   alias alike, and ten remains the convention for a name imported from a register.** The
   floor is where measurement puts it. Over 150,000 sampled Rechtspraak judgments —
   947,625 distinct word forms — every literal of four or five characters the lists have
   carried is reached inside a word that has nothing to do with it: `DDAC` inside *Faddach*,
   `BBIT` inside *rabbits*, `TMAD` inside *Oostmadeweg*, `metam` inside *metamfetamine*, in
   217 documents of which three were the substance. From six characters the picture changes
   in kind rather than in degree: nearly every containing word is the term's own compound or
   inflection — *biocidenverordening*, *bestrijdingsmiddelenresiduen*, *glyfosaathoudend* —
   which is what `substring` exists for.

   > The floor is a floor, not a guarantee. A length rule cannot see that `aldrin` sits
   > inside the surname *Maaldrink* or `captan` inside *mercaptanen*; only measuring a
   > candidate literal against the corpus can. Do that for any short name before giving it
   > `substring` — `scripts/substring_traps.py` is the measurement, and it reads the corpus
   > store rather than the network, so it costs nothing to run.

   That measurement is not optional, and leaving it undone is not cheap. `aldrin` was
   carried as a `substring` literal into the first full EU run, where it matched inside
   **Aldringen** and **Aldringer** — the Luxembourg street at which litigants of the 1950s to
   1970s gave their address for service. It selected 41 ECSC and EEC judgments about coal,
   steel and pricing, each scoring exactly 3.0 on that one term, before anything else in the
   corpus had been read. `en-aldrin` and `nl-captan` are therefore `word`, and `nl-aldrin` is
   `word` with its real Dutch compounds spelled out as aliases. `en-captan` stays `substring`:
   the same scan over 20,000 EU judgments and 214,614 distinct word forms found no English
   word containing it. Each of those is a measurement, not a rule.

2. **`requires`.** Match mode cannot save `beer`. A name that is an ordinary word in the
   jurisdiction's language keeps its weight and its place in the list but is gated on a
   plant-protection term — `en-pesticide` in `eu.json`, `nl-gewasbeschermingsmiddel` in
   `nl.json`. It still reports its match, so the content manager can see it; it just cannot
   qualify a document on its own. This is the same instrument as `nl-drift` and
   `nl-toelatingsbesluit`.

The gate is deliberately generous: gating a name that did not need it costs nothing — any
document genuinely about plant protection has already reached `min_score` on the term that
opened the gate — while missing one costs precision across the whole corpus.

**Micro-organisms are carried as genus and species**, and viruses by their name: a judgment
prints `Bacillus thuringiensis`, never `Bacillus thuringiensis subsp. kurstaki strain
ABTS-351`. One term per species, not one per strain.

### What it costs

The matcher compiles every literal into a handful of tries, so the scan stays proportional
to the length of the text rather than to the number of terms — but "proportional" is not
"free", and a wider trie is a slower one. Measure it: `pytest -s` prints the megabyte
timing, and the budget is **500 ms**. Going from 60 to 860 terms in `nl.json` and 65 to 551
in `eu.json` moved that reading from roughly 290 ms to roughly 360 ms. Report the number in
the pull request that adds the terms.

### Known gap

The EU list carries the Annex's ISO common names in **English only**. Most are
language-invariant, but not all — a German judgment writes `Glyphosat`, not `glyphosate`.
The national spellings live in the national lists, which is where a national judgment is
read; a CJEU judgment in German that names only the German spelling of a substance and no
other term is the residual exposure. Adding the FR/DE/NL columns of the same Annex would
close it.

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

**The loader enforces this**, on the term and on every alias: under `case_sensitive` a
literal may carry no lowercase letter and no space, so `DDT`, `NVWA` and `1907/2006` pass and
`lindaan`, `Ctgb` written as a literal and `European Food Safety Authority` do not. The test
is on character class, not on length, because that is what the failure is about: an
all-capitals rendering of a spelled-out name is still prose, and upper-casing one is not a
way to satisfy the rule. A `match: regex` term is exempt, because its pattern is an
expression — the lowercase characters in `(?<!\w)Ctgb(?!\w)` are syntax.

The rule above was written after the first of these defects and violated twice more before
it was enforced, which is the whole argument for enforcing it: a curator adding an alias
sees one line of JSON, and the attribute that will be applied to it is on another line,
written for a different literal, with no visible effect until someone measures.

An ordinary word inheriting the flag silently loses every sentence-initial occurrence — a
judgment opening "Lindaan is in de bodem aangetroffen" would not have matched, while the
same word mid-sentence would. The split terms `nl-ddt` / `nl-organochloor` and
`nl-ctgb` / `nl-college-toelating` exist for exactly this reason; follow that pattern.

Only set `case_sensitive` where the lowercase form is a common word or would over-match
(`Ctgb`, `DDT`, `REACH`, `NVWA`). Everything else stays case-insensitive.

### Known exceptions

Four authority terms keep a spelled-out name as an alias and are therefore **not** compliant
with the rule above: `nl-nvwa-gewas`, `nl-efsa`, `en-efsa`, `en-echa`.

Each carries **`case_sensitive_exception: true`**, which is how the loader is told, per term
and in the data, that the breach is deliberate. The field is deliberately awkward: it is
refused on a term that is not `case_sensitive`, and refused on a term that does not need it,
so the exception set cannot grow by accident and cannot rot once a term is split. Setting it
is a curation decision with a measured cost — the table below — not a way past a failing
load. Four is the whole set; a fifth needs the same argument these four have.

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
lists tuned.

**Bump `list_version` on every change that can alter a score** — a term, an alias, a weight,
a match mode, a threshold, a band. Note the reasoning in `notes`.

**Do not bump it for a change that cannot** — a corrected comment, a rewritten note, a typo in
prose. `keyword_match` records the version that produced each match, so two versions with
identical scoring behaviour make that record ambiguous: a reader comparing runs would see a
version change and look for a behavioural difference that does not exist.
