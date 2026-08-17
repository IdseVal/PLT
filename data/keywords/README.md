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
| `excluded_nl.json`, `excluded_eu.json` | Terms considered and **rejected**, each with the reason. Not loaded by anything; read before re-adding a term. |

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
  national name as the term and the other spelling as an **alias**, so both file under one
  label. Never give the same literal to two terms — the case would carry two labels for one
  word.
- **Category `active_substance`.** A named substance selects a document on its own, which
  is the point.
- **Check the short and word-like names before you commit them.** See below.

### Substance names that are also ordinary words

A register contains `beer`, `vinegar`, `sucrose`, `urea`, `talc`, `water`, `koper` — which in
Dutch is also a *buyer* — and `jood`, which is also a *Jew*. Each of those would admit and
publish any judgment that happens to contain the word.

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
   steel and pricing, on that one term, before anything else in the corpus had been read. `en-aldrin` and `nl-captan` are therefore `word`, and `nl-aldrin` is
   `word` with its real Dutch compounds spelled out as aliases. `en-captan` stays `substring`:
   the same scan over 20,000 EU judgments and 214,614 distinct word forms found no English
   word containing it. Each of those is a measurement, not a rule.

2. **`requires`.** Match mode cannot save `beer`. A name that is an ordinary word in the
   jurisdiction's language keeps its place in the list but is gated on a plant-protection
   term — `en-pesticide` in `eu.json`, `nl-gewasbeschermingsmiddel` in `nl.json`. It cannot
   select a document on its own, and it labels nothing until its gate opens.

The gate is deliberately generous: gating a name that did not need it costs nothing — any
document genuinely about plant protection has already been selected by the term that opened
the gate — while missing one costs precision across the whole corpus.

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

## Selection: one term is enough

**A document is selected when any curated term matches it.** There is no score, no threshold
and no weight column: a term either belongs in the list, in which case a judgment that uses
it is a pesticide case, or it belongs in `excluded_<code>.json`.

That is the whole rule, and it puts the burden on curation. Before adding a term, ask the
only question that matters:

> Would a judgment that uses this word, and no other word from this list, be a case the
> tracker should hold?

If the answer is no, the term does not go in at a discount — it does not go in.

### What this replaced, and why

The lists used to weight terms 1 to 3 and admit a document that reached a score threshold.
It was removed on 17 August 2026 ([core document §2.13](../../docs/core-document.md)) after
the first full run over both corpora. Weighting had let terms stay in the lists that could
never carry a case — `werkzame stof`, `omwonenden`, `bufferzone`, `NVWA`, `EFSA`, `Wet op de
economische delicten` — on the reasoning that they were harmless below the threshold. They
were not: they combined with each other, and a crop name beside an exposure word was enough
to select a judgment about anything at all. `nl-wed` alone brought in 577 cases.

Those terms are now in `excluded_nl.json` and `excluded_eu.json`, each with the reason it
went. **Read those files before re-adding anything**: a term that was considered and rejected
is a curation decision, not an oversight.

### `requires` is the one instrument that survived

`requires` gates a term on another having matched, and it is doing more work now than it did
under weighting. An active substance whose ISO common name is an ordinary word — `water`,
`beer`, `talc`, `koper` — would otherwise select every judgment that says it. Gated, it
selects nothing on its own and labels nothing; when its gate opens, it labels the case like
any other term.

Roughly ninety terms in each list are gated this way. Gate generously: gating a name that did
not need it costs nothing, because a document genuinely about plant protection has already
been selected by the term that opened the gate.

## Every match is a public label

A case carries the **term** and the **category** of each term that selected it. Both are shown
on the case page and both are filters on the case list, so two things follow for a curator:

- **`term` is written to be read.** It is the label the public sees, and it is the *curated*
  spelling — a case matching `glyphosaat` is listed under `glyfosaat`, because the label comes
  from the term and not from the text.
- **An alias is a spelling of its own term, never a different thing.** A second substance
  filed as an alias labels the case with the wrong chemical. Twelve terms did exactly that —
  atrazine and lindane under `paraquat`, captan and folpet under `mancozeb`, neonicotinoids
  under `glyphosate` in three languages — and each was split into a term of its own.
- **`category` is the second label**, so it is a claim about what kind of thing the term is,
  not a filing convenience.

## The review queue

The queue still exists ([core document §2.7](../../docs/core-document.md)) and still
publishes, records and audits decisions. What changed is that **nothing raises a flag
automatically**. "Borderline" meant "just above the threshold", and there is no threshold; a
content manager raises the flag, and `scoring.review_band` is gone from the schema.

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

**There are none, and the set is empty for a reason worth recording.**

Four authority terms used to breach the acronym-only rule by carrying a spelled-out name as
an alias: `nl-nvwa-gewas`, `nl-efsa`, `en-efsa` and `en-echa`. Each held
`case_sensitive_exception: true`, which is how the loader is told, per term and in the data,
that a breach is deliberate.

The exception was expensive and the cost is worth remembering, because it is the failure mode
`case_sensitive` always has. Under `case_sensitive: true` a spelled-out alias matches **only
the exact string as curated**. Every other rendering scored zero — `Europese Autoriteit voor
Voedselveiligheid` with a capital V, an all-caps heading, a lower-cased mention — and so did
every casing of the acronym itself, so `efsa`, `Efsa` and `nvwa` matched nothing at all.

All four terms were removed on 17 August 2026, not to resolve that, but because a word search
selects on any term and none of these four names a subject: EFSA, ECHA, the NVWA and the RIVM
advise on food, chemicals and health generally. They are in the `excluded_*.json` files.

`case_sensitive_exception` stays in the schema, unused. It is deliberately awkward — refused
on a term that is not `case_sensitive`, and refused on a term that does not need it — so the
exception set cannot grow by accident. A fifth exception would need the argument these four
never really had.

## Curation

These lists are **data curated by the content manager**, not code. Every ingestion run
records which term ids matched each case, so precision and recall can be reviewed and the
lists tuned.

**Bump `list_version` on every change that can alter what is selected or how it is
labelled** — a term, an alias, a category, a match mode, a gate. Note the reasoning in
`notes`.

**Do not bump it for a change that cannot** — a corrected comment, a rewritten note, a typo in
prose. `keyword_match` records the version that produced each match, so two versions with
identical matching behaviour make that record ambiguous: a reader comparing runs would see a
version change and look for a behavioural difference that does not exist.
