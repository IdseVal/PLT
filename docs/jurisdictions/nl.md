# Netherlands — methodology

| | |
| --- | --- |
| **Jurisdiction code** | `NL` |
| **Jurisdiction** | Kingdom of the Netherlands, courts publishing through the Raad voor de rechtspraak |
| **Status** | Connector built and dry-run against the live service; no rows written to the database yet |
| **Keyword list** | `data/keywords/nl.json`, version 1.0.2 (59 terms, Dutch) |
| **Connector** | `plt.pipeline.connectors.rechtspraak` |
| **Endpoints last verified** | 4 August 2026, against `data.rechtspraak.nl` |
| **Document last reviewed** | 5 August 2026 |
| **Author / reviewer** | Documentation agent; endpoint facts inherited from the connector work on issue #7 and Annex 2a |

> **Provenance of the endpoint facts in §3.** None of the measurements below were taken by
> the author of this document. They were taken against the live service on 3 and 4 August
> 2026 by the connector author (issue #7, PR #45) and by whoever corrected Annex 2a
> (PR #56), and are reproduced here with their dates. Where a figure is a sample rather than
> a census, it says so. Nothing in this document has been re-verified since 4 August 2026.

---

## 1. Scope

The `NL` jurisdiction holds decisions of Dutch courts, as published by the Raad voor de
rechtspraak through `data.rechtspraak.nl`. A Dutch court applying Regulation (EC) No
1107/2009 produces an `NL` case; a Court of Justice ruling on a preliminary reference from a
Dutch court produces an `EU` case (see [`eu.md`](eu.md)). The two documents never count the
same decision, and neither aggregates the other: the EU is a jurisdiction in its own right
(`docs/core-document.md` §3.3).

The unit of selection is the **ECLI**, which is also the deduplication key
(`docs/core-document.md` §2.6).

---

## 2. Where the litigation is

### 2.1 One portal, the whole judiciary

`data.rechtspraak.nl` is a **unified portal**, not a court website. The Raad voor de
rechtspraak publishes through it the decisions of the rechtbanken and gerechtshoven, the
Centrale Raad van Beroep, the College van Beroep voor het bedrijfsleven, the Afdeling
bestuursrechtspraak van de Raad van State and the Hoge Raad, together with the conclusies of
the Parket bij de Hoge Raad, disciplinary tribunals, and the courts of the Caribbean parts of
the Kingdom. Seeding the reference tables from the portal's own `Waardelijst/Instanties`
vocabulary produced **261 courts** (verified live, issue #7); the connector classifies them
by the vocabulary's own type — `Rechtbank`, `Kantongerecht`, `Gerechtshof`, `TypeHr`,
`TypeRvS`, `TypeCRvB`, `TypeCBb`, `Parket`, `TuchtrechtelijkeInstantie`,
`Koninkrijksinstantie` and two residual types — rather than by any hard-coded list of court
names (`backend/plt/pipeline/connectors/rechtspraak.py`).

**This matters more than it may appear.** Most Dutch pesticide litigation is first-instance,
so a connector restricted to the two apex courts Annex 2 lists would miss the bulk of the
corpus. The June 2026 dry run (issue #7) bears this out. Of the ten decisions its author read
as unambiguous pesticide litigation, the leading examples were:

| Decision | Forum | Subject |
| --- | --- | --- |
| `ECLI:NL:RBNNE:2026:1051` | Rechtbank Noord-Nederland (first instance) | enforcement request over plant-protection-product use in ornamental cultivation |
| `ECLI:NL:RBNNE:2026:2379` | Rechtbank Noord-Nederland (first instance) | order under penalty against lily growers |
| `ECLI:NL:CBB:2026:200` | College van Beroep voor het bedrijfsleven | Ctgb withdrawing the Azolenprotocol; when a change to a product's *gebruiksvoorschriften* is a *besluit* |
| `ECLI:NL:CBB:2026:248` | College van Beroep voor het bedrijfsleven | Ctgb's extended authorisation of *Gazelle*; time-reinforced toxicity in bees |
| `ECLI:NL:RVS:2026:929` | Afdeling bestuursrechtspraak, Raad van State | spray zone and drift in a planning permission |
| `ECLI:NL:GHSHE:2026:1398` | Gerechtshof 's-Hertogenbosch | veterinary residue / MRL in eggs |

Four families of Dutch pesticide litigation are visible in that list, and they sit at four
different places in the hierarchy:

- **Authorisation and its withdrawal.** Challenges to decisions of the Ctgb (College voor de
  toelating van gewasbeschermingsmiddelen en biociden). Both of the clearest authorisation
  cases in the dry run were **CBb** decisions. *This is an observation from one month of
  data, not a verified statement of the statutory appeal route: the author of this document
  has not checked the Wet gewasbeschermingsmiddelen en biociden to confirm that the CBb is
  the exclusive forum.* Whoever revises this section should confirm it against the statute.
- **Planning and land use.** Spray zones (*spuitzones*), buffer zones and drift in permits and
  zoning plans, heard by the rechtbanken and on appeal by the Afdeling bestuursrechtspraak of
  the Raad van State. Five of the ten clear cases were of this kind.
- **Enforcement.** Orders under penalty and administrative fines against growers, at first
  instance in the rechtbanken, with economic-offence prosecutions under the Wet op de
  economische delicten running through the criminal chain to the Hoge Raad.
- **Residues, food and veterinary safety.** MRL and residue cases, which reach the
  gerechtshoven and the CBb through both the administrative and the criminal routes.

### 2.2 What Annex 2 lists, and how this differs

Annex 2 of the core document lists two Dutch rows: the **Raad van State** and the **Hoge
Raad**. Both are apex courts. Neither is where most of the litigation above is decided, and
the CBb — the forum for the highest-value cases in the corpus — is not listed at all.

The connector does not follow Annex 2 here: it reads the whole portal. That divergence is
deliberate and is recorded in the connector's own documentation, but Annex 2 has not been
amended, so the annex currently understates Dutch coverage. **This is the Dutch instance of
the general warning already attached to Annex 2** after Sweden was added (Annex 2, note of
4 August 2026): the annex is a starting point per member state, not a map of where pesticide
cases are heard. Amending the two Dutch rows to name the portal, the CBb and the
first-instance courts would be an improvement to the annex; it is not this document's to
make.

### 2.3 Out of scope for the `NL` jurisdiction

- **Decisions of the Ctgb itself.** Authorisation decisions are administrative acts, not case
  law, and are not published through this portal. The tracker holds the litigation about
  them, not the decisions themselves.
- **The objection stage (*bezwaar*).** Administrative reconsideration before an authority is
  not case law and does not appear.
- **Judgments never published.** See §6 — the portal publishes a selection, and the size of
  the unpublished remainder is not measurable from the API.
- **Arbitration.** The June 2026 run surfaced a construction arbitration (`ECLI:NL:GHAMS:2026:1519`)
  only because a court reviewed it; arbitral awards as such are not in the corpus.
- **An open question: the Caribbean courts.** The portal carries courts of the Caribbean parts
  of the Kingdom — the connector's vocabulary map has a `Koninkrijksinstantie` type, and the
  dry run matched `ECLI:NL:OGEAM:2025:155`. Those territories are not EU territory, while
  §1.1 of the core document scopes the PLT to "the EU and its member states". Whether their
  decisions belong in the `NL` jurisdiction, in a jurisdiction of their own, or nowhere, has
  not been decided and is not decided here. It is recorded so that a researcher counting
  Dutch cases knows what may be in the count.

---

## 3. How to reach it

### 3.1 Endpoints

| Endpoint | Purpose | Verified |
| --- | --- | --- |
| `https://data.rechtspraak.nl/uitspraken/zoeken` | Atom search feed; discovery | 3 August 2026 (Annex 2a); `return` corrected 4 August 2026 |
| `https://data.rechtspraak.nl/uitspraken/content?id=<ECLI>` | One decision as Rechtspraak XML | 3 August 2026 (Annex 2a) |
| `https://data.rechtspraak.nl/Waardelijst/{Rechtsgebieden,Instanties,Proceduresoorten}` | Controlled vocabularies; seeds the reference tables | 3 August 2026 (Annex 2a); `Instanties` re-verified 4 August 2026 (261 courts, twice, 261 rows) |

The content endpoint returns a Dublin Core `rdf:Description` metadata block, an optional
`inhoudsindicatie` (the editor's summary) and an optional `uitspraak` or `conclusie` body.

### 3.2 The parameters that decide what is selected

**There is no full-text search.** Nothing in the query string can express "pesticides", which
is why topical selection is client-side and why §2.5 of the core document exists at all. The
feed accepts `max` (at most 1000, silently clamped above that), `from` (offset), `date`
(repeatable), `modified`, `subject` (rechtsgebied URI), `creator` (instantie URI), `type`,
`sort` and `return`.

**`return=DOC` is the only accepted value.** `META` is answered with
`400 {"errors":{"Return":["The value 'META' is not valid for Return."]}}`, and so is anything
else (verified 4 August 2026, issue #7). An earlier version of Annex 2a documented
`return=DOC|META`; that was wrong and was corrected in PR #56.

**Omitting the parameter changes the population, not the format.** Without `return`, the feed
yields *all* ECLIs, including registrations that carry metadata and no document body. With
`return=DOC`, it yields only those with a body. The difference is large:

| Measurement | Without `return` | With `return=DOC` | Verified |
| --- | ---: | ---: | --- |
| ECLIs published for 1 July 2026 | **819** | **322** | 4 August 2026 (Annex 2a) |
| ECLIs modified in June 2026 | 21,520 | 10,011 | 4 August 2026 (issue #7 dry run) |

Roughly **60% of published Dutch ECLIs carry metadata but no full text**. That is the single
largest selection decision in this jurisdiction, and it is treated as one: see
§5.1 below, where it is recorded as the jurisdiction's one exception already in force.

**`modified` is read in Europe/Amsterdam local time, while the feed's Atom `updated` is UTC**
(verified 4 August 2026). An explicit offset in the parameter is ignored rather than
honoured, so a caller who sends a UTC instant silently asks for a window one or two hours off
the one it meant. The connector converts on the way out and re-checks every entry against the
caller's UTC bounds on the way in. A single `modified` value is read as a *lower* bound, so a
bounded window must send two.

### 3.3 Discovery, incrementality and what a run costs

Discovery walks the `modified` window with `sort=ASC`, oldest first, paging on `from`/`max`,
and yields entries as they are parsed; the checkpoint is the last modification timestamp
processed (`docs/core-document.md` §2.6). The Atom `updated` element is the source's own
revision marker and is stored as the candidate's content hash, so a weekly re-run skips an
unchanged decision **without spending a request on it**.

The measured cost of the reference run — `plt ingest -j NL --since 2026-06-01 --until
2026-07-01 --dry-run`, at the default two requests per second (issue #7, PR #45):

| | |
| --- | --- |
| Documents in the window (`modified`, `return=DOC`) | **10,011** — the endpoint's own count, and exactly what discovery yielded |
| Feed requests for discovery | 11 |
| Fetched and normalised | 10,011 |
| Errors | **0** |
| Retries, backoffs, HTTP 429s | **0** — the endpoint never once asked the client to slow down |
| Wall time | ~85 minutes, throttle-bound |
| Rows written | 0 (dry run) |

### 3.4 What the source does not expose

- **No topical index usable for pesticides.** The `subject` parameter takes a *rechtsgebied*
  (Bestuursrecht, Civiel recht, Strafrecht, Internationaal publiekrecht, with subfields).
  Pesticide litigation crosses all of them.
- **No indication of why a decision was published.** The publication selection policy is not
  exposed per document (§6).
- **Nothing distinguishes a metadata-only registration in advance** other than its absence
  from the `return=DOC` feed.

Everything the endpoint does expose is kept: the complete Dublin Core block reaches
`case.source_metadata` unflattened, and the raw XML is stored as the document's payload, so a
later reclassification never has to ask the courts for the same judgment twice.

---

## 4. The keyword list

### 4.1 The file and its scoring

`data/keywords/nl.json`, version 1.0.2, updated 3 August 2026: **59 terms**, Dutch only —
38 at weight 3, 7 at weight 2, 14 at weight 1. Scoring: `min_score` **3**,
`count_term_once` true, field multipliers `title` 1.5, `abstract` 1.5, `subject` **1.0**,
`full_text` 1.0.

The one difference from the EU list is the `subject` multiplier: 1.0 here against 1.2 in
`eu.json`. The Dutch `subject` field carries the *rechtsgebied* classification, which is a
four-way division of the whole of Dutch law and can never contain a pesticide term, so
weighting it above `full_text` would buy nothing.

The list carries one **exclusion** — a whole-document veto — for the phrase
*"in een opwelling van drift"*, the criminal-law idiom in which *drift* means a fit of anger
rather than spray drift.

### 4.2 Why these terms

**Language.** Dutch is a compounding language. *Gewasbeschermingsmiddelenrichtlijn* and
*bestrijdingsmiddelengebruik* are single words containing the terms one wants to match, so a
large part of the list matches by `substring` deliberately (`notes` in `nl.json`). That
choice is what gives the list its recall, and it is also the origin of three of the four
candidate exceptions in §5: a substring that is right inside one compound is wrong inside
another.

Two homonyms are already disarmed, and both patterns are worth reusing:

- `nl-drift` (weight 1) carries `requires: ["nl-bespuiting"]`, so *drift* scores nothing
  unless a spraying term also matched. `requires` is an **AND over term ids**; there is no
  way to express "requires any pesticide term" (`data/keywords/README.md`, issue #24).
- The exclusion phrase above vetoes the *opwelling van drift* idiom outright.

Case sensitivity is used only for acronyms whose lower-case form is a common word — `Ctgb`,
`DDT`, `NVWA`, `EFSA`. The flag applies to a term **and all its aliases**, which is why
`nl-ddt`/`nl-organochloor` and `nl-ctgb`/`nl-college-toelating` are split pairs. Two Dutch
terms, `nl-nvwa-gewas` and `nl-efsa`, keep a spelled-out name under `case_sensitive` and are
knowingly non-compliant with that rule; the cost is set out in `data/keywords/README.md`
("Known exceptions") and is being decided under issue #24, not here.

**Legal system.** The national statutes are the highest-precision signals available and have
no equivalent in any other jurisdiction: the **Wet gewasbeschermingsmiddelen en biociden**
with its Besluit and Regeling and the older Bestrijdingsmiddelenwet (`nl-wgb`); the
authorising body **Ctgb** and its full name, the College voor de toelating van
gewasbeschermingsmiddelen en biociden (`nl-ctgb`, `nl-college-toelating`); the authorisation
vocabulary — *toelating*, *toelatingshouder*, *herbeoordeling werkzame stof*, the *wettelijk
gebruiksvoorschrift* (`nl-toelating`, `nl-etikettering-middel`); the Article 38 exemption and
*noodtoelating* (`nl-vrijstelling-art38`); and the **Wet op de economische delicten**
(`nl-wed`), the statute under which pesticide offences are prosecuted. The Union instruments
appear in their Dutch citation forms (`Verordening (EG) nr. 1107/2009`, `528/2012`,
`396/2005`, `Richtlijn 2009/128/EG`), which are language-independent through their numbers.

**Agronomy.** The crops and practices that generate Dutch pesticide litigation, all at weight
1 because a crop alone is not a pesticide case: *lelieteelt* and the lily fields of Drenthe
and Noord-Nederland (`nl-lelieteelt`), bulb growing (`nl-bollenteelt`), tree nurseries,
orchards, arable and glasshouse cultivation (`nl-boomkwekerij`), and the spraying vocabulary
— *bespuiting*, *spuitzone*, *spuitdrift*, *afdrift*, *teeltvrije zone*, *driftreducerende
techniek*. Two of the ten clear cases in the dry run were lily and bulb disputes, which is
the direct evidence that this axis earns its place.

### 4.3 What the first dry run measured

Over June 2026, **38 of 10,011 documents passed** (0.38%). All 38 were read by hand with
their *inhoudsindicatie* (issues #7 and #57):

| Reading | Cases |
| --- | ---: |
| Unambiguous pesticide litigation | 10 |
| Adjacent, a judgement call | 6 |
| Plainly wrong | 22 |

**Strict precision 26%, generous 42%.** The adjacent six are worth naming, because they are
exactly the cases a content manager exists to decide: a PFAS class action against the State,
veterinary residue/MRL in eggs, a biocide case, two slaughterhouse alcohol-wipe fines, and
glyphosate used against Japanese knotweed.

**The score already separates the two populations**, which is the finding that matters:

| Score band | Cases | Clear pesticide | Adjacent | Plainly wrong |
| --- | ---: | ---: | ---: | ---: |
| ≥ 5.0 | 15 | 9 | 6 | 0 |
| 3.0 – 4.5 | 23 | 1 | 1 | 21 |

Twenty-three of the 38 sit in the 3.0–4.5 band and nearly every false positive is in it. The
same shape appeared independently in the EU run, and it is what makes the review queue
(issue #55) the right instrument rather than a threshold change — which §2.7 forbids in any
case. The dry-run report initially recommended raising `min_score` from 3 to 5; that
recommendation was **withdrawn** by its author once §2.7 was recorded, and `min_score`
remains 3.

*A small inconsistency in the source, left visible rather than smoothed over: the report
states 22 plainly wrong cases and that all but one score below 5.0, while its own band table
attributes 21 to the lower band and none to the upper. The discrepancy is one case and does
not affect the conclusion.*

**Recall is not measurable from this run.** There is no labelled ground truth for June 2026.
The strongest statement available is that the cases the connector author expected to find —
the CBb Ctgb decisions, the lelieteelt and bollenteelt disputes, the spuitzone planning
appeals — are all among the 38, and that nothing in the rejected remainder was identified as
a miss. As the starting point for measuring it properly, **61 documents scored between 2.0
and 3.0** and are recorded in the run's JSONL report; sampling that band is what would show
what the threshold currently costs. The report holds one line per document with every term,
its occurrence count and a snippet, so this is re-analysable without another 10,000 requests.

---

## 5. Documented exceptions (§2.10)

The selection method for the Netherlands is the shared one: fetch, filter, rank, recall-first.
What follows is everything this jurisdiction does beyond it.

### 5.1 Register

| # | Status | What it excludes | Instrument | Issue |
| --- | --- | --- | --- | --- |
| 1 | **In force** | ECLIs published without any document body (~60% of the register) | `return=DOC` on the search feed | #7 |
| 2 | **In force** | Documents containing the idiom *"in een opwelling van drift"* | `exclusions` entry in `nl.json` | — |
| 3 | Candidate | Forensic-toxicology boilerplate admitting homicide judgments | match mode / `requires` / exclusion | #57 |
| 4 | Candidate | `hennepkwekerij` matched through the substring `kwekerij` | match mode | #57 |
| 5 | Candidate | `CTB-laag`, cement-bound road base, matched by the alias `CTB` | alias change / match mode | #57 |
| 6 | Candidate | `toelatingsbesluit` in its immigration-law sense | match mode / `requires` | #57 |

Entries 3 to 6 are **candidates only**. `data/keywords/nl.json` has not been edited, and
curation is the content manager's (§2.3). What follows is the evidence and the trade.

### 5.2 Exception 1 — `return=DOC`: excluding ECLIs with no text

**Status.** In force. `rechtspraak_documents_only` defaults to true;
`PLT_RECHTSPRAAK_DOCUMENTS_ONLY=false` mirrors the bare register for anyone who wants it.

**What it excludes.** Roughly 60% of published Dutch ECLIs: registrations carrying Dublin
Core metadata and no `inhoudsindicatie`, no `uitspraak` and no `conclusie` (819 against 322
for 1 July 2026; 21,520 against 10,011 for June 2026).

**Why.** Two measurements, taken 4 August 2026 (issue #7):

1. **The excluded records carry no text at all.** 42 of 42 sampled — 14 each from
   2026-07-01, 2020-07-01 and 2015-06-10 — had neither a summary nor a body, with a median
   payload of 1.8 kB of registration metadata. No stage that reads text could ever select
   one, so excluding them cannot lose a case that would otherwise have been found.
2. **They are not a publication backlog.** The excluded share is flat across eleven years of
   decision dates — 61%, 64%, 66%, 63% and 78% for 2026, 2026, 2024, 2020 and 2015. An
   eleven-year-old registration is still a registration, not a document awaiting publication.

**What it costs.** In principle, any registration that later gains a text and is never seen.
In practice the design recovers that case: when a record gains a body its `modified`
timestamp advances and it enters the `DOC` feed in a later incremental window. The cost of
*not* excluding them is roughly **11,500 further requests a month** on a court's public
endpoint, for a population that no keyword can match.

**Recall impact.** None attainable, on the sample above. This is the strongest form the
argument can take short of a census: the exclusion is justified by the excluded documents
being empty, not by their being unlikely to match.

### 5.3 Exception 2 — the *drift* idiom

**Status.** In force, shipped in `nl.json` since the list was written.

**What it excludes.** Any document containing the phrase *"in een opwelling van drift"*.

**Why.** *Drift* in Dutch means both spray drift and a fit of anger, and the idiom is
standard in criminal judgments. **Note the instrument's blast radius**: an `exclusions` entry
is a whole-document veto — the document is rejected regardless of score and regardless of
what else it says (`backend/plt/pipeline/filters/keywords.py`). It is the bluntest instrument
in the list and should be reserved for phrases that cannot appear in a genuine case.

**What it costs.** A pesticide judgment that also quoted the idiom would be discarded
outright. No such document has been observed. The narrower instrument — `requires:
["nl-bespuiting"]` on `nl-drift` — is also in use and is the better pattern where it suffices.

### 5.4 Candidate exception 3 — `nl-bestrijdingsmiddel` on forensic-toxicology boilerplate

**Status.** Candidate. Not applied.

**What it would exclude.** Judgments containing the standard sentence of a Dutch
pathologist's toxicology report:

> *"…geen aanwijzingen gevonden voor de aanwezigheid van geneesmiddelen, drugs en/of
> bestrijdingsmiddelen."*

**Evidence.** Reproduced against the shipped list: the sentence scores **3.00 and passes** on
`nl-bestrijdingsmiddel` alone (weight 3, `substring`), at exactly `min_score`. It caught
`ECLI:NL:RBGEL:2026:4928` and `ECLI:NL:HR:2022:1864` — two homicide judgments — in a single
month (issue #57).

**Why.** This is the worst of the four defects because it is **boilerplate, not coincidence**.
It recurs across an entire category of criminal judgments and will scale with the whole
criminal corpus. A toxicology screen reporting the *absence* of pesticides is close to the
opposite of a pesticide case.

**What it would cost.** Depends entirely on the instrument:

- A whole-document `exclusions` veto on the sentence would discard any judgment quoting it,
  including a hypothetical poisoning prosecution that recited the screen and then went on to
  concern a pesticide. That is the deliberate false negative §2.10 warns about.
- `requires` on `nl-bestrijdingsmiddel` would be far more damaging: the term is the single
  most productive weight-3 term in the list and gating it would drop genuine cases wholesale.
- The narrowest option is to leave the term alone and let the review queue absorb the
  boilerplate cases, which cost a reviewer a minute each and nothing else.

**Recall impact.** None of the four defects buys any recall, on the connector author's
reading of the run (issue #57). Removing the *boilerplate sentence* specifically — as opposed
to weakening the term — was not observed to cost a single pesticide case in June 2026.

**Who decides.** Content manager (§2.3), via #57.

### 5.5 Candidate exception 4 — `nl-boomkwekerij` matching `hennepkwekerij`

**Status.** Candidate. Not applied.

**What it would exclude.** Cannabis-cultivation judgments matched through the alias
`kwekerij`, which is a substring of `hennepkwekerij`.

**Evidence.** `ECLI:NL:PHR:2026:389`, complicity in cannabis cultivation, with 34 occurrences
of the fragment; measured score **1.00** (issues #7, #57).

**Why.** A *hennepkwekerij* is not a tree nursery. The term itself is correct — the damage is
the `substring` match mode on a short, generic alias.

**What it would cost.** Least of the four. At weight 1 the term does not qualify a document
alone; it is a corroborator of false positives rather than a sole cause. Narrowing `kwekerij`
to a word-boundary match, or dropping the bare alias and keeping `boomkwekerij`, would lose
genuine matches only where a judgment says *kwekerij* without ever naming the crop — a
plausible but unmeasured population. **That population has not been quantified and should be
before the change is made.**

**Recall impact.** Not measured. Unlike exception 3, this one is not free to assess by
inspection.

**Who decides.** Content manager (§2.3), via #57.

### 5.6 Candidate exception 5 — `CTB` matching cement-bound road base

**Status.** Candidate. Not applied.

**What it would exclude.** Civil-engineering judgments referring to *CTB-laag*, a
*cementgebonden* road foundation layer.

**Evidence.** `ECLI:NL:GHAMS:2026:1519`, a construction arbitration reviewed by the
Gerechtshof Amsterdam; measured score **3.00, passes** (issues #7, #57). `CTB` is an alias of
`nl-ctgb` at weight 3.

**Why.** A three-letter acronym at weight 3 qualifies a document alone. `case_sensitive` does
not help here, because the road-base term is upper case too.

**What it would cost.** `CTB` is the former abbreviation of the authorising body (the College
voor de toelating van bestrijdingsmiddelen, before it became the Ctgb), so dropping the alias
loses older judgments that use the old abbreviation — a real but ageing population. Requiring
a word boundary, or requiring that `CTB` not be followed by `-laag`, keeps the historical
match and removes the collision. **The size of the older `CTB` population has not been
measured**; a one-month window from 2026 is the wrong instrument for measuring it.

**Recall impact.** Not measurable from the June 2026 run, which is too recent to contain the
cases the alias exists for.

**Who decides.** Content manager (§2.3), via #57.

### 5.7 Candidate exception 6 — `toelatingsbesluit` colliding with immigration law

**Status.** Candidate. Not applied.

**What it would exclude.** Immigration judgments in which *toelating* means the admission of
an alien.

**Evidence.** `ECLI:NL:OGEAM:2025:155`, an immigration judgment; measured score **3.00,
passes** on the alias `toelatingsbesluit` of `nl-toelating` (weight 3, `phrase`) (issues #7,
#57).

**Why.** *Toelating* means admission generally, and in Dutch administrative practice a
*toelatingsbesluit* is overwhelmingly an immigration decision. **Vreemdelingenrecht is one of
the largest categories in the Dutch corpus**, so this defect scales badly: it is a small
number in one month and a large one over a full backfill.

**What it would cost.** The alias exists because an authorisation decision under the Wgb is
also a *toelatingsbesluit*. Removing it outright would lose authorisation cases that use the
bare word without ever saying *gewasbeschermingsmiddel* — which, on the evidence of the CBb
decisions in the dry run, is unlikely but not impossible. The narrower instrument is
`requires` against a product-class term, at the cost that `requires` is an AND over specific
ids rather than "any pesticide term" (issue #24).

**Recall impact.** None observed in the June 2026 run: the authorisation cases it found all
named the products explicitly.

**Who decides.** Content manager (§2.3), via #57.

### 5.8 Two further observations, recorded but not proposed as exceptions

Both come from the same run and are noted so they are not rediscovered:

- **Three weight-1 contextual terms can reach `min_score` between them.** `nl-residu` +
  `nl-wed` + `nl-nvwa-gewas` scored exactly 3.0 on three sewage-sludge opinions
  (`ECLI:NL:PHR:2026:550`, `:552`, `:553`); `nl-blootstelling` + `nl-nvwa-gewas` + `nl-efsa`
  did the same twice. §2.5 intends contextual terms to qualify only in combination — they are
  combining, just not with anything topical. This is the Dutch instance of the same question
  as the EU list's `en-echa`/`en-reach` pairing ([`eu.md`](eu.md) §5.3) and of issue #24.
- **`nl-werkzame-stof` (weight 2) is ordinary pharmaceutical and narcotics vocabulary** —
  *werkzame stof* / *actieve stof* — and drove four drugs and medicines cases in one month.

Neither is a linguistic accident of the kind §2.10 contemplates; both are questions about
when a term should qualify a document at all, which is a curation question about the weighting
scheme rather than a jurisdiction-specific exception.

---

## 6. Known limitations

1. **The portal publishes a selection.** Not every Dutch judgment is published. The size of
   the unpublished remainder is **not measurable from this API** and has not been established
   from any other source by the author of this document. A researcher must not read the
   tracker's Dutch coverage as a census of Dutch pesticide litigation.
2. **Metadata-only ECLIs are not fetched** (§5.2). They exist, they are roughly 60% of the
   register, and the tracker does not hold them. They contain no text.
3. **Recall has never been measured.** There is no labelled ground truth for any Dutch
   period. The 61 documents scoring 2.0–3.0 in June 2026 are the available sample for a first
   measurement and have not been read.
4. **Precision is currently about 26% strict**, with the false positives concentrated in the
   3.0–4.5 band. Under §2.7 that is handled downstream by the review queue (#55), which does
   not yet exist. Until it does, borderline cases are ingested without any flag.
5. **Four known term defects are unfixed** (§5.4–§5.7) by deliberate choice, pending curation.
6. **The Caribbean question is open** (§2.3).
7. **One language only.** The list covers Dutch. Frisian-language judgments, if any exist in
   the corpus, would not be matched; this has not been investigated.
8. **No rows have been written yet.** Every figure in this document comes from dry runs. The
   tracker's actual Dutch contents are empty at the date of this document.

---

## 7. Verification log

| Date | What was verified | By | Result |
| --- | --- | --- | --- |
| 3 August 2026 | Search, content and vocabulary endpoints; parameter list | Annex 2a | Recorded in Annex 2a |
| 4 August 2026 | `return` accepted values | issue #7 / PR #56 | `DOC` only; `META` → HTTP 400. Annex 2a corrected |
| 4 August 2026 | Population with and without `return` | issue #7 / Annex 2a | 819 vs 322 for 1 July 2026; ~60% metadata-only |
| 4 August 2026 | Content of excluded records | issue #7 | 42/42 sampled across 2015, 2020, 2026 had no text; median 1.8 kB |
| 4 August 2026 | Excluded share by decision date | issue #7 | 61–78%, flat 2015–2026; not a backlog |
| 4 August 2026 | `modified` time zone against Atom `updated` | issue #7 | Parameter Europe/Amsterdam, feed UTC; offsets ignored |
| 4 August 2026 | `Waardelijst/Instanties` seeding | issue #7 | 261 courts, run twice, 261 rows — idempotent |
| 4 August 2026 | Full month against the live service | issue #7, PR #45 | 10,011 documents, 0 errors, 0 retries, 0 backoffs, ~85 min |
| — | Publication selection policy; size of the unpublished remainder | — | **Not verified** |
| — | Statutory appeal route for Ctgb decisions | — | **Not verified** — CBb inferred from two dry-run cases |
| — | Size of the historical `CTB` population (§5.6) | — | **Not measured** |

---

## 8. Sources

- `docs/core-document.md` §1.1 (scope), §2.3 (roles), §2.5 (keyword lists), §2.6
  (deduplication), §2.7 (no false negatives), §2.8 (transparent, explainable, repeatable),
  §2.9 (this document's requirement), §2.10 (exceptions), §3.3 (EU as its own jurisdiction),
  Annex 2 and Annex 2a. §2.7–§2.10 arrive via PRs #54 and #58.
- `backend/plt/pipeline/connectors/rechtspraak.py` — the connector, and the four endpoint
  properties verified on 4 August 2026 that drove its design.
- `backend/tests/integration/test_rechtspraak_live.py` — the opt-in contract tests that
  re-check the time-zone and `return` assumptions against the live service.
- `backend/plt/config.py` — `rechtspraak_*` settings, including `rechtspraak_documents_only`.
- `data/keywords/nl.json` and `data/keywords/README.md`, including the "Known exceptions"
  section on case sensitivity.
- `backend/plt/pipeline/filters/keywords.py` — the matcher, and the whole-document semantics
  of an `exclusions` entry.
- **Issue #7**, comments of 4 August 2026 — the June 2026 dry run and its correction in the
  light of §2.7.
- **Issue #57** — the four term defects, each reproduced against the shipped list.
- **Issue #24** — contextual authority terms and the limits of `requires`.
- **Issue #55** — the review queue that §2.7 puts in place of a threshold change.
