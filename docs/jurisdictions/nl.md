# Netherlands — methodology

| | |
| --- | --- |
| **Jurisdiction code** | `NL` |
| **Jurisdiction** | Kingdom of the Netherlands, courts publishing through the Raad voor de rechtspraak |
| **Status** | Connector built and dry-run against the live service; no rows written to the database yet |
| **Keyword list** | `data/keywords/nl.json`, version 1.2.0 (60 terms, Dutch) |
| **Connector** | `plt.pipeline.connectors.rechtspraak` |
| **Endpoints last verified** | 4 August 2026, against `data.rechtspraak.nl` |
| **Document last reviewed** | 5 August 2026 |
| **Author / reviewer** | Documentation agent; endpoint facts inherited from the connector work on issue #7 and Annex 2a |

> **Provenance of the endpoint facts in §3.** None of the measurements below were taken by
> the author of this document. They were taken against the live service on 3 and 4 August
> 2026 by the connector author (issue #7, PR #45) and by whoever corrected Annex 2a
> (PR #56), and are reproduced here with their dates. Where a figure is a sample rather than
> a census, it says so. No endpoint fact has been re-verified since 4 August 2026. The
> keyword measurements in §5.4–§5.7 are the exception: they were taken on 5 August 2026
> against the two list versions themselves, and §7 says by whom.

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

### 1.1 The Caribbean courts of the Kingdom are in

> **Decision by the project owner, 5 August 2026, on issue #62.** Recorded here under §2.9
> because it is a scope decision and not an implementation detail. It confirms the behaviour
> the connector already had; what changes is that the behaviour is now chosen.

`data.rechtspraak.nl` publishes the judgments of the courts of the **Caribbean parts of the
Kingdom** alongside the European Dutch ones, and the connector reads them because it reads
the whole portal. Those decisions are part of the `NL` jurisdiction. They are **not**
excluded, and they are **not** split into a jurisdiction of their own.

What a reader has to know about them:

- **They are Kingdom territory and not EU territory.** Aruba, Curaçao and Sint Maarten are
  constituent countries of the Kingdom; Bonaire, Sint Eustatius and Saba are special
  municipalities of the Netherlands. All six are Overseas Countries and Territories, outside
  the customs union and outside the territorial scope of EU law. **Regulation (EC) No
  1107/2009 does not apply there**, nor does Regulation (EU) No 528/2012 or Directive
  2009/128/EC.
- **So a Dutch filter returns cases governed by a different substantive legal order.** A
  Caribbean pesticide judgment applies local landsverordeningen, not the Union régime that
  every other case in this jurisdiction turns on. Nothing about the score, the terms or the
  ECLI marks that difference, and the Dutch keyword list matches them anyway because the
  legal Dutch of the Caribbean courts is the same language.
- **The reason they are in.** Excluding them would be a deliberate false negative
  (`docs/core-document.md` §2.7), against a population of genuine, published pesticide
  judgments of a Dutch court, to enforce a territorial rule the tracker's users are unlikely
  to be applying when they filter by jurisdiction. Under §2.10 an exclusion carries the
  higher burden of justification, and this one was not carried: the size of the Caribbean
  pesticide population has never been counted, so the cost of excluding it is unknown.
  Splitting them into their own jurisdictions is the most accurate option and remains open;
  it needs its own keyword lists (§2.5) and its own map treatment, and nothing here forecloses
  it.

**How a reader tells them apart.** Two ways, and the difference between them matters:

| Signal | Where it lives | Usable today |
| --- | --- | --- |
| `Koninkrijksinstantie`, the court type in the portal's own `Waardelijst/Instanties` vocabulary | `court.source_type`, verbatim; the whole vocabulary entry is in `court.source_metadata` | **Yes, in the database** (issue #72, 5 August 2026) — `_COURT_TYPES` still maps it to level `other` alongside `AndereGerechtelijkeInstantie`, because that is what the normalised classification means, but the raw type is now stored beside the normalisation instead of being discarded. `SELECT … WHERE source_type = 'Koninkrijksinstantie'` answers the question directly. **No API filter or frontend facet yet** — see §6 |
| The **ECLI court code** and the court's name | `case.ecli`, `court.name`, `court.abbreviation` | **Yes** — `ECLI:NL:OGEAM:2025:155` is the Gerecht in eerste aanleg van Sint Maarten (verified against the live endpoint, issue #62), and `OCHM` is the Constitutioneel Hof Sint Maarten (from the portal's vocabulary, `backend/tests/fixtures/rechtspraak/instanties.xml`). No exhaustive list of the Caribbean court codes has been compiled |

The vocabulary lists **16** Kingdom courts, every one of them at level `other` (verified live,
5 August 2026, issue #72). The information exists at the source exactly once, when the
vocabulary is read, and until #72 it was **lost in normalisation** — which is why keeping it
was worth a column of its own rather than a later migration: recovering it afterwards would
have meant asking the portal for every court record again (`docs/architecture.md` rule 2.6).

**Two things followed from fixing it, and the second was not expected.** For these courts, and
only for these courts, the portal qualifies the attribute that identifies the deciding court:
`psi:resourceIdentifier` in the `psi.rechtspraak` scheme, where a European Dutch judgment
writes a bare `resourceIdentifier` in `overheid.RechterlijkeMacht`. The connector read only the
unqualified form, so **every Kingdom judgment failed to resolve against the vocabulary**: it
fell through to a key derived from the court's name, matching nothing, and was stored with no
level, no domain and no type, against a `court` row of its own beside the one seeding had
already created for the same court. Both are fixed together; `ECLI:NL:OGEAM:2025:155` is the
recorded fixture that pins it.

What is still not built is the *filter*: nothing in the HTTP API or the frontend exposes
`court.source_type`, so the query above is one a person with database access can run and a
user of the tracker cannot. §6 carries that, narrowed.

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
names (`backend/plt/pipeline/connectors/rechtspraak.py`). Since issue #72 the type itself is
stored on the row as well as the level it maps to, so the classification can be re-derived
from the database rather than from the portal.

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

**Not** out of scope: the Caribbean courts of the Kingdom. That was an open question when
this document was written and is now a decision — they are in, see [§1.1](#11-the-caribbean-courts-of-the-kingdom-are-in).

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

`data/keywords/nl.json`, version 1.2.0, updated 5 August 2026: **60 terms**, Dutch only —
39 at weight 3, 7 at weight 2, 14 at weight 1; by match mode, 31 `substring`, 21 `phrase`,
5 `word` and 3 `regex`. Scoring: `min_score` **3**, `review_band` **2.5**, `count_term_once`
true, field multipliers `title` 1.5, `abstract` 1.5, `subject` **1.0**, `full_text` 1.0.

The one difference from the EU list is the `subject` multiplier: 1.0 here against 1.2 in
`eu.json`. The Dutch `subject` field carries the *rechtsgebied* classification, which is a
four-way division of the whole of Dutch law and can never contain a pesticide term, so
weighting it above `full_text` would buy nothing.

The list carries one **exclusion** — a whole-document veto — for the phrase
*"in een opwelling van drift"*, the criminal-law idiom in which *drift* means a fit of anger
rather than spray drift. It is still the only one: the four exceptions decided on issue #57
(§5.4–§5.7) all narrow a *pattern* instead of vetoing a *document*, for the reason §5.3
gives.

The three `regex` terms are those exceptions. `match: regex` is what the schema offers "for
the rare case the others cannot express", and this is that case: a lookaround expresses
"this word, except in this one context", which no other mode can. The measured price is a
scan per expression rather than one shared trie pass — **1 MB of full text took 90 ms
against version 1.1.0 and 165 ms against 1.2.0** (3 trie scans became 3 tries plus 9
expressions), against a budget of 500 ms asserted in
`backend/tests/unit/test_keyword_filter.py`.

### 4.2 Why these terms

**Language.** Dutch is a compounding language. *Gewasbeschermingsmiddelenrichtlijn* and
*bestrijdingsmiddelengebruik* are single words containing the terms one wants to match, so a
large part of the list matches by `substring` deliberately (`notes` in `nl.json`). That
choice is what gives the list its recall, and it is also the origin of three of the four
exceptions in §5: a substring that is right inside one compound is wrong inside another.

Three homonyms are disarmed, and the patterns are worth reusing:

- `nl-drift` (weight 1) carries `requires: ["nl-bespuiting"]`, so *drift* scores nothing
  unless a spraying term also matched. `requires` is an **AND over term ids**; there is no
  way to express "requires any pesticide term" (`data/keywords/README.md`, issue #24).
  `nl-toelatingsbesluit` now uses the same instrument (§5.7).
- The exclusion phrase above vetoes the *opwelling van drift* idiom outright.
- A lookaround inside a `regex` term removes one *occurrence* without touching the term or
  the document (§5.4–§5.6).

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
| 3 | **In force** | *bestrijdingsmiddel* inside the forensic-toxicology enumeration *"geneesmiddelen, drugs en/of bestrijdingsmiddelen"* | negative lookbehinds on `nl-bestrijdingsmiddel`, `match: regex` | #57 |
| 4 | **In force** | *kwekerij* inside a compound, e.g. `hennepkwekerij` | left word boundary on one alias of `nl-boomkwekerij`, `match: regex` | #57 |
| 5 | **In force** | `CTB-laag`, cement-bound road base, matched by the alias `CTB` | negative lookahead on `nl-ctgb`, `match: regex` | #57 |
| 6 | **In force** | *toelatingsbesluit* qualifying a document with no plant-protection term in it | `nl-toelatingsbesluit` split out with `requires: ["nl-gewasbeschermingsmiddel"]` | #57 |

Entries 3 to 6 were candidates until 5 August 2026, when the project owner decided them on
issue #57 and they were applied in `nl.json` 1.2.0. Each section below states **what it
excludes, why, and what it costs**, as §2.10 requires, and each cost is a measurement rather
than an estimate: every figure in §5.4–§5.7 comes from running both list versions over the
same probe documents, and the reproductions are the ones recorded in #57. **The higher burden
§2.10 puts on an exclusion is why all four narrow a pattern rather than reject a document,
and why none of the four removes a term.**

> **What the measurement is, and is not.** The four defects were reproduced from the
> sentences #57 quotes, not from the judgments themselves — the June 2026 run wrote no rows
> and its JSONL report is not in the repository, so the corpus the defects were found in
> cannot be re-scored here. Each cost below is therefore *what the change does to a document
> of that shape*, verified in both directions, and not a count of cases in a corpus. Re-running
> the June window against 1.2.0 is what would turn these into population figures, and it has
> **not** been done.

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

### 5.4 Exception 3 — `nl-bestrijdingsmiddel` on forensic-toxicology boilerplate

**Status.** In force since `nl.json` 1.2.0, 5 August 2026 (issue #57).

**What it excludes.** One *occurrence*, not a document and not a term: the word
*bestrijdingsmiddel(en)* where it stands inside the enumeration of a Dutch toxicology screen,

> *"…geen aanwijzingen gevonden voor de aanwezigheid van geneesmiddelen, drugs en/of
> bestrijdingsmiddelen."*

**Instrument.** `nl-bestrijdingsmiddel` moves from `substring` to `match: regex`, and its
pattern carries three negative lookbehinds — `drugs en/of `, `drugs en `, `drugs of `. The
word is otherwise matched exactly as before, inside compounds included, and the redundant
plural alias is gone because the expression already matches inside it. **The guard is anchored
on *drugs*, so it can only suppress an occurrence standing in a narcotics enumeration.**

**Evidence and effect, measured against both list versions.**

| Document | 1.1.0 | 1.2.0 |
| --- | --- | --- |
| The screen sentence, as #57 quotes it (homicide judgment) | **3.00, passes** | **0.00, rejected** |
| The same with *"drugs en bestrijdingsmiddelen"*, across a line break | 3.00, passes | 0.00, rejected |
| The same with *"drugs of bestrijdingsmiddelen"* | 3.00, passes | 0.00, rejected |
| *"het gebruik van bestrijdingsmiddelen op het perceel"* | 3.00, passes | **3.00, passes** |
| The compound *bestrijdingsmiddelengebruik* | 3.00, passes | **3.00, passes** |
| The screen sentence **plus** *"in de maaginhoud is het bestrijdingsmiddel parathion aangetroffen"* | 3.00, passes | **3.00, passes** |

**Why this instrument.** The last row is the whole argument. An `exclusions` veto on the
sentence — the obvious instrument, and the one §5.3 already uses for *drift* — would have
rejected that document outright, discarding a poisoning prosecution because it quoted a
negative screen for one sample. A lookbehind removes the occurrence and leaves the term, so
any other mention of a pesticide anywhere in the judgment still scores its full 3. Under §2.7
that difference is the difference between a false positive and a false negative.

**What it costs.** A document whose *only* pesticide reference is the word standing in that
enumeration no longer passes on this term. By construction such a document mentions pesticides
only as an item on a list of things not found. No case of any other shape was observed to
change: of the 26 probe documents run against both versions, the only ones this exception
moved are the three enumerations above.

**Residual.** The lookbehinds are fixed-width, which is a limitation of Python's `re`, so a
single whitespace character between *en/of* and the word is tolerated but two are not: with a
double space the sentence still scores 3.00 and still passes. That failure is deliberate in
its direction — it fails *open*, back to the status quo, where the case scores exactly
`min_score` and is caught by the review band (#55). A word-order variant that puts
*bestrijdingsmiddelen* first in the list is also not covered.

### 5.5 Exception 4 — `nl-boomkwekerij` matching `hennepkwekerij`

**Status.** In force since `nl.json` 1.2.0, 5 August 2026 (issue #57).

**What it excludes.** The bare alias *kwekerij* where it stands inside a compound —
`hennepkwekerij` being the observed case (`ECLI:NL:PHR:2026:389`, 34 occurrences of the
fragment).

**Instrument.** `nl-boomkwekerij` moves to `match: regex`, and the bare alias becomes
`(?<!\w)kwekerij`: a word boundary on the **left only**. The right-hand side stays open, which
is what keeps the inflections a compounding language needs. Every other pattern of the term —
`boomkwekerij`, `fruitteelt`, `boomgaard`, `akkerbouw`, `glastuinbouw` — is an unanchored
expression and so keeps exactly its `substring` behaviour.

**Evidence and effect.**

| Document | 1.1.0 | 1.2.0 |
| --- | --- | --- |
| Cannabis-cultivation judgment, *hennepkwekerij* | **1.00** | **0.00** |
| *"op het perceel wordt een kwekerij geëxploiteerd"* | 1.00 | **1.00** |
| *"de boomkwekerijen aldaar"* | 1.00 | **1.00** |
| *"appellante drijft een plantenkwekerij"* | 1.00 | **0.00** |

**Why this instrument.** Dropping the alias would have cost the second row — a judgment that
says *kwekerij* without naming the crop — which §5.5 of the previous revision flagged as a
plausible but unmeasured population. A left boundary keeps it. The term itself was never
wrong; the `substring` mode on a short generic alias was.

**What it costs.** Every other `-kwekerij` compound the list does not name: *plantenkwekerij*,
*rozenkwekerij*, *viskwekerij*. A genuine nursery case written only as *plantenkwekerij* loses
one contextual point. That is the smallest cost of the four — this is a weight-1 term that
cannot qualify a document alone, so the loss is a corroborator, never a case. It is
nonetheless a **real deliberate false negative** and is why the boundary was put on the left
only rather than on both sides, which would additionally have cost the plural *kwekerijen*.

### 5.6 Exception 5 — `CTB` matching cement-bound road base

**Status.** In force since `nl.json` 1.2.0, 5 August 2026 (issue #57).

**What it excludes.** The alias `CTB` where it is immediately followed by `-laag` or `-lagen`
— *cementgebonden* road foundation, the civil-engineering sense
(`ECLI:NL:GHAMS:2026:1519`, a construction arbitration reviewed by the Gerechtshof Amsterdam).

**Instrument.** `nl-ctgb` moves to `match: regex`, `case_sensitive` unchanged. The lookarounds
`(?<!\w)`/`(?!\w)` reproduce exactly the word boundaries `match: word` supplied, and one
negative lookahead `(?!-la(?:ag|gen))` is added to the alias. **The historical abbreviation is
kept.**

**Evidence and effect.**

| Document | 1.1.0 | 1.2.0 |
| --- | --- | --- |
| *"de aannemer heeft een CTB-laag van 25 centimeter aangebracht"* | **3.00, passes** | **0.00, rejected** |
| *"het CTB heeft de toelating destijds verlengd"* | 3.00, passes | **3.00, passes** |
| *"het Ctgb heeft het middel toegelaten"* | 3.00, passes | **3.00, passes** |

**Why this instrument.** `CTB` is the former abbreviation of the authorising body — the College
voor de toelating van bestrijdingsmiddelen, before it became the Ctgb — so dropping the alias
would silently drop older judgments that use it. The size of that historical population has
never been measured (§7), and §2.10 does not permit an exclusion whose cost is unknown when a
narrower instrument exists. The lookahead is that instrument: it removes the collision and
keeps the recall.

**What it costs.** Nothing that was measured, and by construction only documents in which the
letters `CTB` are followed by `-laag`. A genuine pesticide judgment would have to write
`CTB-laag` for this to cost anything.

**Residual.** The guard is on the hyphenated form because that is the form the evidence
contains. A judgment calling the layer a bare *CTB* — *"de aannemer heeft de CTB
aangebracht"* — still scores 3.00 and still passes. Widening the guard to *cementgebonden*
vocabulary generally was rejected as excluding more than the evidence supports; if a second
construction case appears, this is the place to revisit.

### 5.7 Exception 6 — `toelatingsbesluit` colliding with immigration law

**Status.** In force since `nl.json` 1.2.0, 5 August 2026 (issue #57).

**What it excludes.** The word *toelatingsbesluit* as a **sole qualifier**: it no longer
carries a document over `min_score` unless a plant-protection term is present somewhere in the
same document. The observed case is `ECLI:NL:OGEAM:2025:155`, an immigration judgment, where
*toelating* means the admission of an alien.

**Instrument.** `toelatingsbesluit` is **split out** of `nl-toelating` into a term of its own,
`nl-toelatingsbesluit`, weight 3, `phrase`, carrying `requires: ["nl-gewasbeschermingsmiddel"]`
— the instrument `nl-drift` already uses. The gate is on the split term alone, which is the
point of splitting: `toelatingshouder`, `toelatingsaanvraag` and *herbeoordeling werkzame stof*
stay in `nl-toelating`, ungated, because no evidence implicates them and gating them would have
been an exclusion beyond the evidence. Matching is otherwise identical — the alias was already
`phrase`, and it stays `phrase`.

**Evidence and effect.**

| Document | 1.1.0 | 1.2.0 |
| --- | --- | --- |
| Immigration judgment, *toelatingsbesluit van de minister* | **3.00, passes** | **0.00, rejected** (reported as gated, not missing) |
| CBb-style appeal against a *toelatingsbesluit* of the College voor de toelating van gewasbeschermingsmiddelen en biociden | 12.00, passes | **15.00, passes** |
| *"het toelatingsbesluit betreft een biocide"*, no plant-protection word | 6.00, passes | **3.00, passes, and now flagged for review** |
| *toelatingshouder* / *toelatingsaanvraag* / *herbeoordeling werkzame stof*, each alone | 3.00 / 3.00 / 5.00, all pass | **unchanged** |

**Why this instrument.** *Vreemdelingenrecht* is one of the largest categories in the Dutch
corpus, so the defect scales badly over a backfill: a small number in one month, a large one
over ten years. The gate holds because the authority's own name — *College voor de toelating
van **gewasbeschermingsmiddelen** en biociden* — contains the term it requires, so any judgment
naming the body opens the gate on its own. `requires` is an AND over one id and cannot express
"any pesticide term" (issue #24), which is the reason the gate names the single most productive
product-class term rather than the right one for every case.

**What it costs.** Two things, both stated because they are the price of the decision:

1. **A biocide-only authorisation case loses three points.** Row three: it still passes on
   `nl-biocide`'s own weight of 3, but at 3.00 rather than 6.00 it now falls inside the review
   band `[3, 5.5)` and is queued for a content manager. That is extra review load on a genuine
   case, not a lost case.
2. **A pesticide authorisation judgment that says *toelatingsbesluit* and never any
   plant-protection word would be lost.** None was observed — the authorisation cases the dry
   run found all named the products explicitly — but this is the deliberate false negative
   §2.10 requires to be named, and it is unmeasured.

**Side effect.** Row two: where both the phrase and the split alias occur, the document now
scores 3 points more than it did, because `count_term_once` counts once per term id and there
are now two ids. It is an arithmetic consequence of the split, it moves genuine authorisation
cases further above the review ceiling rather than below it, and no probe changed its pass or
review status because of it.

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
5. **Two residuals remain from the four term exceptions** (§5.4–§5.7): the toxicology guard is
   defeated by a double space before the word, and the `CTB` guard by the road base written
   without its hyphen. Both fail *open* — the document passes, scores at `min_score` and is
   caught by the review band — which is the direction §2.7 asks a failure to take.
6. **Caribbean cases are in the Dutch jurisdiction, and the API still cannot filter them out**
   (§1.1). Half of this is fixed: since issue #72 the portal's own `Koninkrijksinstantie` is
   persisted as `court.source_type`, so the corpus can be split on it in SQL and the record no
   longer stays silent about which cases those are. What does not exist is a query parameter
   or a facet: `court.source_type` is exposed nowhere in the HTTP API (§5 of
   `docs/architecture.md`) or the frontend, so **a researcher using the tracker still receives
   cases to which EU pesticide law does not apply without being able to include or exclude
   them.** A follow-up needs a criterion on `CaseSearchCriteria`, a join in `search_cases`, the
   value in `list_facets`, and a facet in the UI.
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
| 5 August 2026 | The four #57 reproductions against `nl.json` 1.1.0 and 1.2.0 | issue #57 / PR for `fix/57-nl-exceptions` | Each scored 3.00/1.00 and now scores 0.00; 26 probe documents run against both versions, tabulated in §5.4–§5.7 |
| 5 August 2026 | Cost of each exception, in both directions | same | Recorded per exception in §5.4–§5.7; no probe that passed for a pesticide reason stopped passing |
| 5 August 2026 | Matching cost of the three `regex` terms | same | 1 MB of full text: 90 ms at 1.1.0, 165 ms at 1.2.0, budget 500 ms |
| 5 August 2026 | Kingdom courts in the live `Waardelijst/Instanties` | issue #72 | **16**, every one of them typed `Koninkrijksinstantie` and normalised to level `other` |
| 5 August 2026 | `ECLI:NL:OGEAM:2025:155` end to end against the live endpoint | issue #72 | Before: court unresolved, `level`/`domain`/`source_type` all null, key derived from the court's name. After: `source_identifier` `http://psi.rechtspraak.nl/GEASM`, `abbreviation` `OGEAM`, `level` `other`, `source_type` `Koninkrijksinstantie` |
| 5 August 2026 | The identifying attribute on `dcterms:creator` | issue #72 | Qualified as `psi:resourceIdentifier` (scheme `psi.rechtspraak`) for the Kingdom courts, bare `resourceIdentifier` (scheme `overheid.RechterlijkeMacht`) for the European Dutch ones; confirmed on `ECLI:NL:OGEAM:2025:155` and `ECLI:NL:OGHACMB:2025:1` |
| — | Publication selection policy; size of the unpublished remainder | — | **Not verified** |
| — | Statutory appeal route for Ctgb decisions | — | **Not verified** — CBb inferred from two dry-run cases |
| — | Size of the historical `CTB` population (§5.6) | — | **Not measured** |
| — | Effect of `nl.json` 1.2.0 over the June 2026 corpus | — | **Not measured** — the run wrote no rows and its report is not in the repository (§5.1) |
| — | Number of Caribbean judgments in the corpus, and how many are pesticide cases (§1.1) | — | **Not counted** — countable in SQL since #72 (`court.source_type = 'Koninkrijksinstantie'`), but no run has written the rows to count |

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
- **Issue #57** — the four term defects, each reproduced against the shipped list, and the
  owner's decision of 5 August 2026 to handle them as documented exceptions (§5.4–§5.7).
- **Issue #62** — the Caribbean Kingdom courts, and the owner's decision of 5 August 2026 to
  include them in the `NL` jurisdiction (§1.1).
- **Issue #72** — the portal's court type, discarded at ingestion until 5 August 2026 and now
  persisted as `court.source_type`, together with the qualified `psi:resourceIdentifier` that
  had been keeping the Kingdom courts from resolving against the vocabulary at all (§1.1, §6).
- **Issue #24** — contextual authority terms and the limits of `requires`.
- **Issue #55** — the review queue that §2.7 puts in place of a threshold change.
