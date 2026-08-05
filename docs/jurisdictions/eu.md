# European Union — methodology

| | |
| --- | --- |
| **Jurisdiction code** | `EU` |
| **Jurisdiction** | European Union — the Court of Justice of the European Union |
| **Status** | Connector built and dry-run against the live service; no rows written to the database yet |
| **Keyword list** | `data/keywords/eu.json`, version 1.0.2 (65 terms; English, French, German, Dutch) |
| **Connector** | `plt.pipeline.connectors.eurlex` |
| **Endpoints last verified** | 4 August 2026, against the CELLAR SPARQL and REST endpoints |
| **Document last reviewed** | 5 August 2026 |
| **Author / reviewer** | Documentation agent; endpoint facts inherited from the connector work on issue #8 and Annex 2a |

> **Provenance of the endpoint facts in §3.** None of the measurements below were taken by
> the author of this document. They were taken against the live CELLAR service on 3 and
> 4 August 2026 by the connector author (issue #8, PR #49) and recorded in Annex 2a of the
> core document, and are reproduced here with their dates. Nothing has been re-verified since
> 4 August 2026.

---

## 1. Scope

**The European Union is a jurisdiction in its own right, never an aggregate of its member
states.** That is a design decision of the project, recorded in `docs/core-document.md` §3.3
and carried through the schema, the map and this connector: the map shows an EU marker
alongside the member states rather than a colour derived from them, and an `EU` case is
counted once, as an EU case, and never again inside a national total.

The consequence for a researcher is precise. A Court of Justice ruling on a preliminary
reference from a Dutch court is an `EU` case; the Dutch court's own judgment, before and
after the reference, is an `NL` case (see [`nl.md`](nl.md)). Neither document double-counts
the other, and a query for "pesticide cases in the Netherlands" will not return *Blaise*.

The unit of selection is the **CELEX number**, which is also the deduplication key
(`docs/core-document.md` §2.6). One CELEX is one case, however many language versions and
however many cellar works stand behind it.

---

## 2. Where the litigation is

### 2.1 The courts, and what reaches them

The EU jurisdiction is the **Court of Justice of the European Union**: the Court of Justice
and the General Court, together with the Civil Service Tribunal for the period it existed.
The connector resolves the deciding court from the corporate body CELLAR credits the decision
to, by authority URI rather than by name.

Its output, for the whole of 2024 (measured, issue #8):

| Document type | Count |
| --- | ---: |
| Court of Justice judgments | 501 |
| General Court judgments | 493 |
| Advocate General opinions | 217 |
| Orders | 337 |
| **Total decisions fetched** | **1,548** |

Advocate General opinions are collected deliberately. They are not judgments and are stored
as opinions, but in pesticide cases they are frequently the fullest statement of the legal
argument and a researcher looking for the reasoning would not forgive their absence.

Four routes carry pesticide litigation to these courts, and the dry run found all four:

- **Annulment actions before the General Court**, typically by NGOs or by producers against
  Commission approval decisions: PAN Europe `T-536/22`, and the biocides cases Troy Chemical
  `T-297/21`, Dakem `T-341/23` and SBM Développement `T-667/22`.
- **Preliminary references to the Court of Justice** on the interpretation of Regulation (EC)
  No 1107/2009 and its predecessors: *Blaise* `C-616/17`, on glyphosate, is the canonical
  example.
- **Appeals from the General Court**: PAN Europe `C-308/22` and `C-309/22` on emergency
  authorisations of neonicotinoid-treated seeds; Commission v Pollinis France on the bee
  guidance documents.
- **Access-to-documents and Aarhus litigation** about pesticide evidence — a distinct family,
  and the one that most often produces genuine cases scoring on contextual terms alone.

### 2.2 What Annex 2 lists, and how this differs

Annex 2 lists three EU rows: the CJEU (`curia.europa.eu`), EUR-Lex and the European
e-Justice Portal. Those are publication websites. The route actually used is neither of the
first two directly but **CELLAR**, the Publications Office's repository behind EUR-Lex,
reached through its SPARQL and REST interfaces (Annex 2a). No decision is fetched from
`curia.europa.eu`, and the e-Justice Portal is not used at all.

### 2.3 Out of scope for the `EU` jurisdiction

- **National courts applying EU pesticide law.** Those belong to their own jurisdictions.
- **The Commission, EFSA and the Board of Appeal of ECHA.** Administrative and quasi-judicial
  bodies, not courts. Their decisions appear in the tracker only as the subject of litigation.
- **CELEX sectors other than 6.** Regulations, directives and implementing acts are not case
  law. They appear as citations from cases, which is how an EU pesticide-regulation citation
  graph becomes possible later, but they are not cases.
- **Editorial summaries and Official Journal notices** (`SUM_JUR`, `ABSTRACT_JUR`,
  `INFO_JUDICIAL`). Deliberately not enumerated: including them discovers every case several
  times over (`backend/plt/config.py`, `eurlex_resource_types`).

---

## 3. How to reach it

### 3.1 Endpoints

| Endpoint | Purpose | Verified |
| --- | --- | --- |
| `https://publications.europa.eu/webapi/rdf/sparql` | CELLAR SPARQL 1.1 over the CDM ontology; enumerates CELEX sector 6 | 3 August 2026 (Annex 2a); query facts re-verified 4 August 2026 |
| `http://publications.europa.eu/resource/celex/<CELEX>` | CELLAR REST; metadata notice and full-text manifestations | 4 August 2026 (issue #8) |
| EUR-Lex SOAP web service | Supports full-text queries; **requires registered credentials and cannot return document files**. Kept as a fallback, not used | 3 August 2026 (Annex 2a) |

### 3.2 The five properties that shape everything

**1. The 10,000-result cap.** Since **1 January 2026** a single CELLAR search returns at most
10,000 results. Discovery may therefore not issue one unbounded query: it walks date windows
(30 days initially), counts each window before paging it, and halves any window that reaches
the cap until it fits — down to a floor of one hour, past which it processes the window anyway
and says so in the log rather than splitting for ever. A window the count finds empty costs no
page query at all.

**2. One case per CELEX; language versions are documents, not cases.** A CELEX number resolves
to several cellar works — the complex work, its members, and rectified versions carrying
`do_not_index` — and to one expression per language. The discovery query groups by CELEX and
aggregates over the works, so a case is discovered once however many works stand behind it,
and each retrieved language becomes a `case_document` row on the one case. Verified twice
over: asserted on the normalised case, and asserted against the database, where ingesting the
same CELEX twice with a second language configured leaves one `case` row carrying more
documents (issue #8).

**3. Three things that cost documents silently** (all verified 4 August 2026, issue #8;
Annex 2a corrected accordingly):

- **`Accept: text/html` is answered with a 404** for most judgments; the same document is
  served as `application/xhtml+xml`. Older judgments *do* come back as `text/html`, so the
  connector offers both and parses whichever arrives. A client that offered only `text/html`
  would conclude that CELLAR holds no text for the modern corpus.
- **A parenthesised CELEX number must be percent-encoded into the path.** A corrigendum or a
  second order in a case carries a suffix — `62021TO0601(01)` — and CELLAR 404s the unencoded
  form while serving `62021TO0601%2801%29`. Left alone, corrigenda would have failed every
  week, and a failed document holds the checkpoint back by design.
- **`cdm:resource_legal_id_sector` is typed `xsd:string`.** An untyped `"6"` in a SPARQL
  filter matches nothing *silently*: the query succeeds and returns an empty result set,
  which is indistinguishable from a quiet week.

**4. The language fallback, and what it recovered.** A judgment exists in up to 24 languages.
The connector retrieves the configured preference (English by default), falls back to the
procedural language, and then to whichever language CELLAR actually holds — and it passes over
a language that answers with a server error rather than failing the document. That last rule
came from `62022TJ0371`, which **answers 500 for English every time while serving French and
German normally**, and which ended the first two dry runs at exactly the same document.

The effect was far larger than the case that revealed it. **Before the fallback, 22% of
decisions came out with no full text at all; over the 704 decisions run after it, 1% did**
(issue #8). What had looked like "CELLAR holds only a notice for a fifth of the corpus" was
almost entirely "the English manifestation is missing while another language is there for the
asking". That is the largest single recall gain in the project's work so far, and it was
invisible until the connector ran against the live repository at scale — which is the
strongest available argument for §2.9's requirement that endpoint behaviour be verified
rather than assumed.

**5. Not every decision has a retrievable full text.** Some carry only a metadata notice, in
any language and any format. The pipeline stores the notice and moves on rather than treating
it as a failure.

Which date bounds the discovery window is configurable. In the default `modification` mode it
bounds CELLAR's own `lastModificationDate`, which is what makes a weekly run pick up newly
published *and* newly corrected decisions, and what the checkpoint means. In `document` mode
it bounds the date of the decision itself, for backfilling a historical period; candidates
then carry no modification instant, so a backfill cannot push the incremental checkpoint
forward over work it never looked at.

### 3.3 What a run costs, and how the endpoint behaved

The reference run walked 1 January to 31 December 2024 in `document` mode at two requests per
second (issue #8, PR #49): **1,548 decisions discovered and fetched, 0 failed documents,
0 rows written**. Over the year CELLAR returned enough transient `5xx` responses to trigger
**25 backoffs, all of which recovered**, and **ten individual language versions could not be
served at all** and were passed over for another language.

### 3.4 What the source does not expose

- **No party field.** CELLAR exposes none; the connector reads the parties out of a segment of
  the title, which CJEU titles structure as *court and date # parties # keywords # case
  number*.
- **No usable law-domain classification.** The CDM classification is a policy tree ("Internal
  policy of the European Union → Chemicals → Plant protection products"), not the
  public/private/criminal distinction the PLT schema asks for. The connector therefore leaves
  `law_domain` and `law_subfield` **empty** rather than guessing: in a research database a
  wrong classification is worse than a missing one. EU cases will consequently be blank in any
  filter built on that field until a mapping is decided.

Everything CELLAR does expose is kept: the notice XML verbatim, each manifestation as its own
document, the citation relations as CELLAR states them (`cites`, `interprets`,
`declares_valid`, `declares_void`, `applies`, `based_on`, `incorporates`) rather than
flattened to "cites", and every CDM field without a column of its own in `source_metadata`.
*Blaise* yields 24 citations; *Bayer* yields 91.

---

## 4. The keyword list

### 4.1 The file and its scoring

`data/keywords/eu.json`, version 1.0.2, updated 3 August 2026: **65 terms** across four
language sections — English (41), French (9), German (9), Dutch (6) — of which 45 are at
weight 3, 12 at weight 2 and 8 at weight 1. Scoring: `min_score` **3**, `count_term_once`
true, field multipliers `title` 1.5, `abstract` 1.5, `subject` **1.2**, `full_text` 1.0. No
exclusions.

The `subject` multiplier of 1.2 is the only scoring difference from the Dutch list, and it is
justified: the EU `subject` field carries the CDM subject-matter and descriptor labels, which
are curated topical vocabulary and can legitimately contain a pesticide term, unlike the Dutch
four-way *rechtsgebied* classification.

### 4.2 Why these terms

**Language.** This is the axis on which the EU differs from every member state: a judgment
exists in up to 24 languages and the tracker holds whichever it could retrieve. The list
therefore carries English plus the three most common procedural languages for pesticide cases,
and `NormalisedCase.full_text` joins every retrieved language version, so a curated term in
any of them qualifies the case. That design is why the list is multilingual and why §5.2 —
identical literals firing several language terms — is an EU-specific problem that does not
arise in a monolingual jurisdiction.

**Legal system.** The instrument numbers are the highest-precision signals this jurisdiction
has, and they are the only **language-independent** ones: `1107/2009` (plant protection
products), `528/2012` (biocidal products), `396/2005` (maximum residue levels), `2009/128`
(sustainable use), `91/414` (the predecessor directive), `1185/2009` (pesticide statistics).
They match in a Bulgarian judgment as readily as in an English one, which matters
disproportionately given the language coverage gap in §6. Alongside them sit the procedural
vocabulary of EU pesticide law — approval and non-renewal of active substances, zonal
authorisation and mutual recognition, the Article 53 emergency derogation, SCoPAFF and the
rapporteur Member State — and the contextual chemicals-regime terms REACH, CLP, ECHA, EFSA
and Aarhus, all at weight 1.

**Agronomy.** Largely absent, and correctly so. The EU courts do not try spray-drift disputes
between neighbours; they review approvals, refusals and access to the science behind them. The
list carries the science rather than the farm: endocrine-disrupting properties, bee health and
pollinators, operator and bystander exposure, seed treatment and treated seeds, integrated pest
management.

### 4.3 What the first dry run measured

Over the whole of 2024, **54 of 1,548 decisions passed** (3.5%); 0 failed, 0 rows written. All
54 were read by hand (issues #8 and #51):

| | |
| --- | --- |
| Passed the filter | 54 |
| Genuinely pesticide or biocide cases | **~17 — precision about one third** |

**Recall is the reassuring half.** The cases a pesticide lawyer would name first come out top
of the ranking by a wide margin: PAN Europe `C-308/22` and `C-309/22` at 58.5 and 60.5, PAN
Europe `T-536/22` at 53.5, Commission v Pollinis France at 20.0, and the biocides cases at
18–19.5. Nothing in the rejected remainder was identified as a miss. A smaller, hand-checkable
run makes the point at a glance: **on 1 October 2019 the Court delivered eight decisions and
the filter passed exactly one — *Blaise*, on glyphosate and Regulation (EC) No 1107/2009, at
47.5 — with the other seven at 0.0.**

**The score distribution separates the two populations sharply:**

| Score | Cases | Reading |
| --- | ---: | --- |
| ≥ 12 | 18 | almost all genuinely in scope |
| 6 – 12 | 8 | mixed |
| 4 – 6 | 7 | mixed, thinning out |
| **3.0 – 3.9** | **21** | **almost none in scope** |

The bottom band is 40% of everything that passed and holds nearly all the false positives.
That shape produced a concrete proposal — raising `min_score` from 3 to 6 would drop 21 cases,
of which perhaps two are in scope — and **the project owner declined the trade**: 19 false
positives removed at the price of roughly two genuine cases is not a trade this project makes,
because a missed judgment is the expensive error. `min_score` stays at 3, and precision is
handled downstream by the review queue (issue #55). That decision is now §2.7 of the core
document, and this run is the evidence §2.7 cites.

> **An evidence-hygiene note worth keeping.** The first figures reported for this run —
> 1,055 decisions, 37 passed, precision ~40% — came from a dry run believed to have stopped
> early. It had in fact completed, and the corrected figures are the ones above (issue #51,
> comment of 5 August 2026). The correction changed the counts but not the conclusion, and
> one stray reference to "the rejected 1,018" survives in the issue #8 report where the
> completed run rejected 1,494. Cite the corrected figures.

---

## 5. Documented exceptions (§2.10)

The selection method for the EU is the shared one: fetch, filter, rank, recall-first. There
are **no exceptions in force** for this jurisdiction — `eu.json` carries no `exclusions` entry
and no `requires` clause. Two candidates are below, and neither has been applied.

### 5.1 Register

| # | Status | What it would change | Instrument | Issue |
| --- | --- | --- | --- | --- |
| 1 | Candidate | Identical literals scoring once per language section (`pesticide` scores 9) | matcher change, or a curation rule against duplicate literals | #51 |
| 2 | Candidate | `en-echa` + `en-reach` reaching `min_score` between them | gating / `requires` / weighting | #51, #24 |

Both are **candidates only**. `data/keywords/eu.json` has not been edited, and curation is the
content manager's (§2.3).

### 5.2 Candidate exception 1 — duplicated literals across language sections

**Status.** Candidate. Not applied. **Note that this one is a scoring-integrity issue rather
than an exclusion**, and so does not carry §2.10's higher burden — it removes no document from
the corpus.

**What it would change.** Today `count_term_once` counts per **term**, so a literal spelled
identically in several languages fires several terms and is credited several times. Measured
against the shipped list (issue #51):

| Word | Score | Terms fired |
| --- | ---: | --- |
| `pesticide` | **9.00** | `en-pesticide`, `fr-pesticide`, `nl-eu-pesticide` |
| `herbicide` | 6.00 | `en-herbicide`, `fr-herbicide` |
| `glyphosate` | 6.00 | `en-glyphosate`, `fr-glyphosate` |

**Nine literals are shared across terms in `eu.json`.** One occurrence of the word
*pesticide* in an English judgment scores three times the threshold on the strength of a
single word — which is how a drinking-water case (`C-481/22`, trihalomethanes) and a European
Arrest Warrant case (`C-202/24`) reached the top of a ranking they have no business being in.
Seventeen of the 54 were affected.

**Why it is not a false-positive fix.** All nine shared literals are at weight 3, and a
weight-3 term qualifies a document on its own by design. **The inflation therefore distorts
the score, not the verdict — today.** It is a trap rather than a curiosity for two reasons:

1. Under §2.7 the score's job is no longer to admit or reject; it is to **sort genuine cases
   from borderline ones so the review queue is meaningful**. A case scoring 9 instead of 3
   lands in the confident band on one word, which is precisely the misplacement the queue
   exists to catch.
2. It will flip verdicts the moment a duplicated literal is weight 1 or 2. Three weight-1
   terms sharing a literal would reach `min_score` unaided — a contextual term qualifying a
   document alone, which the weighting scheme exists to prevent.

**What it would cost.** Nothing in recall under either remedy, which is unusual and worth
saying plainly:

- *Credit a literal once however many terms claim it* (a matcher change, mirroring how
  `count_term_once` already works per term). More robust, and it survives future list edits.
  It changes the shared matcher, so under §2.10 it is a change to the method rather than to
  this jurisdiction's inputs, and it would need to be right for every jurisdiction.
- *Forbid duplicate literals across language sections* (a curation rule). Keeps the matcher
  simple, but it is a rule that has to be re-obeyed on every future edit by every curator, and
  it is a rule this list has already broken nine times.

**Two authoring slips stand regardless**, and are cheap to fix: `insecticide` is an alias of
`fr-herbicide`, and `neonicotinoid` an alias of `de-glyphosat`. Both are copy-paste errors in
list authorship rather than translations, acknowledged as such by the list's author.

**Who decides.** Content manager (§2.3), via #51.

### 5.3 Candidate exception 2 — `en-echa` and `en-reach` reaching the threshold alone

**Status.** Candidate. Not applied.

**What it would exclude.** REACH and ECHA chemicals litigation that contains no
pesticide-specific term at all.

**Evidence.** Both terms are weight 1 and `min_score` is 3, so the pair plus any third
contextual term — and a third is easy to come by in a chemicals judgment — admits the case.
**Twelve of the 54 passed this way** (issue #8): an appeal about access to harmonised
standards (`C-588/21`), an oxo-degradable plastics case (`T-745/20`), lead in ammunition
(`C-105/23`), and several REACH fee and animal-testing appeals. This is the single largest
leak in the EU list.

**Why.** REACH and ECHA are the general EU chemicals regime. They are genuinely relevant
*context* in a pesticide case and genuinely irrelevant in a plastics or ammunition case, and
nothing in the current scheme can tell the two apart, because weight expresses how strong a
signal is and not what it must be corroborated by.

**What it would cost — and this is the entry where §2.10's higher burden bites.** ECHA and
REACH terms are exactly what carries the **access-to-documents and Aarhus family** of genuine
pesticide cases, which often discuss the chemicals regime at length before reaching the
pesticide at issue. Gating them behind a pesticide term would be safe only if every such case
also names a pesticide, product class or instrument number somewhere in the retrieved text —
plausible, but **not measured**, and the run provides no figure for it. A case reaching the
tracker on ECHA and REACH alone and being genuine is precisely the false negative §2.7
refuses.

There is also a mechanical obstacle: `requires` is an **AND over specific term ids**, so
"requires any pesticide term" cannot currently be expressed (`data/keywords/README.md`, issue
#24). Gating these two would mean either enumerating a long AND — which is wrong, since it
would demand *all* of them — or a change to the matcher.

**Recall impact.** Unmeasured, and that is the reason to be slow here rather than the reason
to act. Under §2.7 an unmeasured recall cost is not a small one; it is an unknown one.

**Who decides.** Content manager (§2.3), via #51 and #24, which the run's author and the
project owner both note should be decided together.

### 5.4 Two further observations, recorded but not proposed as exceptions

Both come from the same run and both concern homonymy rather than scoring:

- **Trade-mark judgments quote the Nice Classification.** Class 5 reads "Fungicides,
  herbicides", so an EUIPO case listing class 5 goods scores on a product-class term without
  concerning pesticides at all — Drinks Prod (twice), Unilab, Galenica, EvivaMed,
  Interapotheek, Certinvest. **EUIPO cases are a large share of the General Court's docket**,
  so this scales with the corpus rather than staying a curiosity. The suggestion on the record
  is to exclude a Nice-classification goods list from `full_text` scoring; no one has costed
  it, and it would need to survive the case where a genuine dispute concerns a pesticide brand.
- **`en-active-substance` is pharmaceutical vocabulary too.** A genuine homonym — *active
  substance* means the same thing in medicines law — which on its own carried two
  medicinal-products cases (Mylan, D&A Pharma) over the line. This is the EU analogue of the
  Dutch `nl-werkzame-stof` observation ([`nl.md`](nl.md) §5.8) and may want the `requires`
  treatment `nl-drift` already demonstrates.

---

## 6. Known limitations

1. **Seven procedural languages have no section in the list.** In 2024 a quarter of decisions
   had no English full text and were stored in the procedural language: German, French,
   Spanish, Italian, Polish, Bulgarian, Greek, Portuguese, Romanian, Dutch and Hungarian.
   `eu.json` covers four of those. For the rest, **only the language-independent signals — the
   instrument numbers — can match**, and a case that never gets an English version stays
   invisible to the four language sections unless it cites an instrument by number. This is
   partly self-healing, because CELLAR's modification date changes when a translation is added
   and the weekly run then re-fetches; but it is a real, quantified recall gap today.
2. **`law_domain` and `law_subfield` are empty for every EU case** (§3.4), deliberately.
3. **Precision is about one third**, with the false positives concentrated in the 3.0–3.9
   band. Under §2.7 that is handled by the review queue (#55), which does not yet exist.
4. **Recall has not been measured against ground truth.** The available evidence is the
   ranking (the canonical cases come top) and the 1 October 2019 hand-check (eight decisions,
   one passed, and it was the right one). Neither is a recall measurement.
5. **Two known list defects are unfixed** (§5.2, §5.3) by deliberate choice, pending curation.
6. **The 10,000-result cap is a moving constraint.** It was imposed on 1 January 2026 and
   could be tightened again; the window-halving strategy adapts, but a much lower cap would
   change the cost of a backfill materially.
7. **One language version can be permanently broken while the others are fine**
   (`62022TJ0371`). The fallback handles it, and the tracker then holds that case in a
   language the list may not cover — see limitation 1.
8. **No rows have been written yet.** Every figure in this document comes from dry runs.

---

## 7. Verification log

| Date | What was verified | By | Result |
| --- | --- | --- | --- |
| 3 August 2026 | SPARQL and CELLAR REST endpoints; the 10,000-result cap | Annex 2a | Recorded in Annex 2a |
| 4 August 2026 | Content negotiation for a manifestation | issue #8 / PR #49 | `text/html` 404s for most judgments; `application/xhtml+xml` serves them; older ones are `text/html` |
| 4 August 2026 | Parenthesised CELEX in the REST path | issue #8 | Must be percent-encoded; `62021TO0601(01)` 404s unencoded |
| 4 August 2026 | Typing of `cdm:resource_legal_id_sector` | issue #8 | `xsd:string`; an untyped `"6"` returns an empty result set silently |
| 4 August 2026 | Language availability | issue #8 | `62022TJ0371` answers 500 for English every time; French and German serve normally |
| 4 August 2026 | Effect of the language fallback | issue #8 | Decisions with no full text fell from 22% to 1% over 704 decisions |
| 4 August 2026 | Whole of 2024 against the live service | issue #8, PR #49 | 1,548 decisions, 0 failures, 25 recovered backoffs, 10 unservable language versions |
| 4 August 2026 | One case per CELEX, in the database | issue #8 | Same CELEX twice with a second language: one `case` row, more documents |
| — | Whether Aarhus/ECHA-only genuine cases always name a pesticide term (§5.3) | — | **Not measured** |
| — | Cost of excluding Nice-classification goods lists (§5.4) | — | **Not measured** |
| — | EUR-Lex SOAP web service | — | **Not verified** — credentials never obtained; not used |

---

## 8. Sources

- `docs/core-document.md` §2.5 (keyword lists), §2.6 (deduplication), §2.7 (no false
  negatives), §2.8 (transparent, explainable, repeatable), §2.9 (this document's requirement),
  §2.10 (exceptions), §3.3 (the EU as its own jurisdiction), Annex 2 and Annex 2a. §2.7–§2.10
  arrive via PRs #54 and #58.
- `backend/plt/pipeline/connectors/eurlex.py` — the connector, and the five source properties
  that shape it.
- `backend/tests/integration/test_eurlex_live.py` — the opt-in contract tests that re-check
  the sector typing, the content negotiation and the CELEX encoding against the live service.
- `backend/plt/config.py` — `eurlex_*` settings: the cap, the window strategy, the language
  preference, the resource types and the discovery-date mode.
- `data/keywords/eu.json` and `data/keywords/README.md`, including the "Known exceptions"
  section on case sensitivity, which covers `en-efsa` and `en-echa`.
- **Issue #8**, comment of 4 August 2026 — the 2024 dry run, the language-fallback measurement
  and the three CELLAR corrections.
- **Issue #51** — the two causes of the EU list's precision, the corrected run figures, and the
  project owner's decision not to raise `min_score`.
- **Issue #24** — contextual authority terms and the limits of `requires`.
- **Issue #55** — the review queue that §2.7 puts in place of a threshold change.
