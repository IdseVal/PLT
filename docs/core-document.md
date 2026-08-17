# Pesticide Litigation Tracker — Core document

**Law group, Wageningen University & Research**
Edwin Alblas, Idse Val & Vincent Latjes

| | |
| --- | --- |
| Original version | 28 June 2026 (`Core document.pdf`) |
| This version | 3 August 2026 |
| Status | **Living document.** This Markdown file supersedes the PDF. All updates go here. |

---

## 1. Project brief

This document provides an overview of the purpose, data sources, and functional
requirements of the Pesticide Litigation Tracker (PLT) project, initiated by Wageningen
University & Research's Law chair group. This overview serves as a comprehensive blueprint
for stakeholders involved in the project, guiding front- and back-end development, as well
as server and infrastructure development later. This document is updated throughout the
project and across its operational phases to remain up to date.

### 1.1 Project goal and target audience

The goal of the PLT is to provide an open-access, automatically updated database of
pesticide-related case law within the jurisdictions of the EU and its member states. The
[Sabin Center's Climate Litigation Tracker](https://climatecasechart.com/) serves as an
example.

Within the project's scope, **pesticide-related cases** are defined as public, private and
criminal law cases that centre on the effects, governance and/or liability of pesticide
admission, trade and/or use.

The PLT is intended for use by scholars and researchers, legal professionals, NGOs, civil
servants, and others working on or interested in legal developments related to pesticide
governance within the European Union and its member states. While potentially applicable
for commercial use, the PLT is made available on an open-access basis to enhance its use
in academic research and by NGOs.

### 1.2 Service provision

The PLT provides an overview of all available, online-published pesticide-related case law.
In doing so, the PLT is designed to automatically scrape cases from online platforms such
as EUR-Lex (EU) and rechtspraak.nl (NL), based on the criteria described in §1.1. An
overview of the data sources from which these cases are scraped is included in
[Annex 2](#annex-2-project-data-sources).

---

## 2. Functional requirements

This project aims to incorporate the features and data (including classification)
described in this section. It also outlines the different user roles involved in the PLT.

### 2.1 Initial features

1. **User-friendly interface**, primarily designed to support comfortable reading of
   available case law.
2. **Cross-language, multilingual search engine.** The PLT provides English and
   original-language searching, so users can find relevant cases published in languages
   other than English, with those cases made available in their original language.
3. **Full-text document search** across all available court documents from the selected
   data sources.
4. **Advanced filtering.** The search engine is equipped with filters based on the
   classification in §2.2.
5. **Downloadable data.** Users may select and download all available case law, including
   metadata.
6. **Manual addition of case law by administrators.**

### 2.2 Data and classification

Selected case law is published in original language, full text. Each case is classified and
presented with a case abstract and a link to its original publication webpage. The
classification labels adopted are:

1. **Jurisdiction**
2. **Law domain** (public, private & criminal)
3. **Law subfield** (e.g. public; administrative)
4. **Litigating parties**
5. **Date of filing, and verdict**
6. **Topic** (precise classification to be further developed during the project)

Beyond these classification labels, the pipeline stores **as much source metadata as each
endpoint exposes** — court/instance, procedure type, case number(s), publication date,
decision date, seat/location, legal area, language, source references, citation links, and
the raw source payload — so that later classification work never requires a re-fetch.

### 2.3 User roles

Upon deployment, the PLT is operated by three different users as detailed below; the fourth
role is that of PLT users.

1. **System admin** — manages website configuration, integration management, security, and
   maintenance. Once the PLT is up and running, a third-party contractor takes up this role.
2. **Communication coordinator** — forms the bridge between system admin, content
   management, and users. Fulfilled by a member of the Law chair group.
3. **Content manager** — actively keeps track of added case law to verify eligibility
   according to set criteria and guarantee metadata is processed correctly. Fulfilled by a
   member of the Law chair group. **This role owns the keyword lists described in §2.5.**
4. **User** — end-users of the PLT.

### 2.4 Potential future features

1. API access with filtering options and bulk download feature.
2. User registration and the possibility to add annotations to listed case law.
3. Litigation from jurisdictions outside of the EU.
4. Emailing list with alerts on new cases.
5. FAQ.
6. Visualisation of data insights.

### 2.5 Linguistic filtering and per-jurisdiction keyword lists

> *Added 3 August 2026. This section records a structural constraint on the data pipeline.*

None of the source endpoints offer a reliable topical filter for "pesticides". Case law
must therefore be selected by **filtering on language**: candidate documents are fetched
from a jurisdiction's endpoint and matched against a curated list of terms that occur in
pesticide litigation in that jurisdiction (for the Netherlands, e.g. `lelieteelt`,
`pesticiden`, `gewasbeschermingsmiddelen`).

**Consequence for the project: such a keyword list must exist for every jurisdiction that
is added to the database.** A jurisdiction cannot be onboarded until its list exists,
because the terms are language- and system-specific:

- **Language.** A Dutch list will not retrieve German, French, Polish or Greek cases. Each
  list is written in the working language(s) of that jurisdiction's courts. Multilingual
  jurisdictions (Belgium, Luxembourg, Malta, Cyprus, Ireland, and the EU itself) need
  multiple language sections within one list.
- **Legal system.** Terms include national statutes, authorities and procedures — the
  Dutch list carries `Ctgb` and `Wet gewasbeschermingsmiddelen en biociden`; a French list
  would carry `ANSES` and `Code rural`. These have no cross-jurisdiction equivalent.
- **Agronomy.** Crop and practice terms that signal pesticide litigation differ by country:
  bulb and lily cultivation in the Netherlands, viticulture in France, olive groves in
  Greece.

Design consequences:

1. Keyword lists are **data, not code** — one JSON file per jurisdiction in
   `data/keywords/`, validated against a shared schema, versioned in git.
2. Selection is a **word search**: a document is in scope when **any** curated term matches
   it. Every term therefore has to be specific enough to carry a case on its own, and a term
   that is not belongs in `excluded_<code>.json` rather than in the list. See §2.13, which
   replaced the weighted scoring this point used to describe.
3. Lists are **curated by the content manager**, not by developers. Every ingestion run
   records which terms matched each case, so precision and recall of the lists can be
   reviewed and the lists tuned over time.
4. Linguistic filtering is **filter stage 1**. The pipeline's filter chain is pluggable so
   later stages (classifier models, citation-based filters, manual review queues) can be
   added without rewriting the connectors.

### 2.6 Deduplication and incremental ingestion

> *Added 3 August 2026.*

The pipeline runs on a schedule (weekly) and must be safely re-runnable:

- Every case is keyed on its **source identifier** (ECLI for the Netherlands, CELEX for the
  EU), unique per jurisdiction. Incoming documents are checked against the database before
  insertion, so no duplicates are created.
- Runs are **incremental**: each connector records a checkpoint (the last processed
  modification timestamp) and on the next run fetches only what changed since.
- A content hash per document detects genuine upstream revisions, which update the existing
  record rather than creating a second one.

### 2.7 Selection policy: no false negatives, and a review queue

> *Added 4 August 2026. Decision by the project owner, following the first live dry run.*

**The PLT optimises for recall, not precision.** A missed judgment is the expensive error: a
false positive costs a content manager a minute, a false negative is a case the tracker
implicitly claims does not exist. An open-access research database is judged on what it
fails to contain, and a researcher cannot audit an absence.

Two consequences bind the filter chain:

1. **Selection is not tightened by arithmetic.** Any proposal to narrow what the tracker
   holds must be assessed by **what it loses**, counted rather than estimated.
2. **Precision is handled in curation.** A term that admits the wrong cases comes out of the
   list, with the reason recorded, rather than being kept at a discount. See §2.13.

**The content manager may be a person or an agent.** That is deliberately undecided, so the
review queue must not assume either — the same queue, record and audit trail has to serve
both.

> *Amended 17 August 2026.* This section originally bought recall with a low score threshold
> and gave the resulting false positives to a review queue, flagged automatically by their
> distance above that threshold. §2.13 replaced the threshold, so nothing is flagged
> automatically any more: the queue and its audit trail remain, and a content manager raises
> the flag. The recall-first principle above is unchanged — what changed is where precision
> is bought.

### 2.8 Methodology must be transparent, explainable and repeatable

> *Added 4 August 2026. Standing constraint on all selection and classification work.*

The PLT publishes its methodology (see the site's Methodology page) because a research
database that cannot account for its own contents is not usable as a source. Three
requirements follow, and they bound what the filter chain may become:

- **Transparent.** How a case was selected is public, not internal. The criteria, the term
  lists and the thresholds are published artefacts.
- **Explainable.** For any individual case it must be possible to say *why it is in the
  database* — which terms matched, in which field, against which version of which list. This
  is what `keyword_match` records, and why it is not optional. Since §2.13 those records are
  also public: the terms and their categories are the labels a case is listed under, so the
  explanation is on the case's own page rather than in an internal table.
- **Repeatable.** Re-running the same selection over the same corpus with the same list
  version must produce the same result. Lists are versioned data in git; scores are
  deterministic; every run is recorded in `ingest_run`.

**A technique that cannot meet all three is out of scope, however well it performs.** This
applies directly to the "later stages" contemplated in §2.5: a classifier that improves
precision but cannot explain an individual verdict, or cannot be re-run to the same answer,
does not qualify for this pipeline as it stands. If such a stage is ever wanted, the
requirement above has to be revisited deliberately, not worked around.

### 2.9 Onboarding a jurisdiction: the jurisdiction methodology document

> *Added 5 August 2026. Decision by the project owner.*

Every jurisdiction added to the database gets its own **methodology document** at
`docs/jurisdictions/<code>.md`, written *before* its connector. It is a precondition for
onboarding, alongside the keyword list required by §2.5.

Each document records:

1. **Where the litigation actually is.** Which courts and instances hear pesticide cases in
   that jurisdiction, and why. Annex 2 lists apex courts almost exclusively, and most
   pesticide litigation never reaches one — spray-drift disputes, authorisation challenges
   and residue prosecutions are largely first-instance and often specialised. Sweden's Land
   and Environment Courts are the worked example (§Annex 2). Getting this wrong does not
   produce a visible error; it produces a jurisdiction that looks covered and is not.
2. **How to reach it.** The endpoints, their parameters, their quirks and their limits,
   each **verified against the live service** with the date of verification. Annex 2a is the
   summary; the jurisdiction document is where the detail belongs.
3. **The keyword list**, and the reasoning behind its jurisdiction-specific terms.
4. **Documented exceptions** — see §2.10.
5. **Known limitations**, including anything the source does not expose.

### 2.10 One method, explicit exceptions

> *Added 5 August 2026.*

The selection method is **the same for every jurisdiction**: fetch, filter, rank, with the
recall-first policy of §2.7. Jurisdictions differ only in their *inputs* — the endpoints and
the keyword list — not in how selection works.

Where a jurisdiction genuinely needs more, it is added as an **explicit, documented
exception** in that jurisdiction's methodology document, never as an undocumented adjustment
to shared code. The Dutch list supplies the motivating cases: the forensic-toxicology
boilerplate *"geen aanwijzingen … geneesmiddelen, drugs en/of bestrijdingsmiddelen"* admits
homicide judgments, and `kwekerij` matches `hennepkwekerij`. These are linguistic accidents
of one language, not facts about pesticide litigation, and they do not belong in shared
logic.

Every exception must state **what it excludes, why, and what it costs**. Because an exclusion
is a deliberate false negative — the error §2.7 says this project does not accept — the
justification carries a higher burden than an inclusion. An exception that cannot be
explained on the Methodology page (§2.8) does not qualify.

### 2.11 Quarantine: bounding a permanently failing document

> *Added 5 August 2026. Decision by the project owner.*

A document that fails repeatedly must not stall its jurisdiction's window for ever. The rule:
**after N consecutive runs in which the same `source_id` fails, the pipeline advances past it
and records the fact durably.**

- **N is configuration, not a constant.** The default is **3** — with weekly runs, roughly
  three weeks of transient upstream trouble is tolerated before the window is allowed to move
  on, which is long enough to absorb an outage and short enough not to lose a quarter.
- **The record is a durable, queryable quarantine**, not a log line: jurisdiction, source id,
  first and last failure, attempt count, last error, and whether it has since been resolved.
- **Quarantine must be visible.** A quarantined document is a potential missing case, which
  is precisely the error §2.7 refuses. It is therefore surfaced for review in the same way a
  borderline case is (§2.7), not buried in operational telemetry. Silently skipping is the
  one behaviour this rule exists to prevent — advancing past a document is acceptable only
  because a human or agent will see that it happened.
- A quarantined document is **retried on later runs** rather than abandoned; quarantine
  releases the window, it does not close the case.
- **A quarantined document's continued failure does not count toward run status.** Once the
  pipeline has advanced past it, the run has done everything available to it, and a run that
  processed its whole window successfully is a **success** even while a quarantined document
  keeps failing in the background. Otherwise every run after the first quarantine reports
  `partial` for ever, `/api/health` freezes permanently for that jurisdiction, and the alarm
  that §2.11 exists to raise becomes the one nobody reads.

  The two questions are separate and get separate signals:

  | Question | Signal |
  | --- | --- |
  | Did this run work? | run status, and the scheduled job's exit code |
  | What are we persistently unable to fetch? | the quarantine record, surfaced for review |

  Conflating them is what makes an unattended weekly job untrustworthy: an alarm that is
  always on carries no information, and an alarm that never fires carries none either. Run
  status must be able to return to green while quarantine keeps its own count.

### 2.12 Subscriber data: pseudonymise on unsubscribe

> *Added 5 August 2026. Decision by the project owner.*

The PLT offers an email alert list. Subscriber addresses are **personal data** and Wageningen
University is an EU controller, so GDPR Article 5(1)(e) — storage limitation — applies:
personal data is kept no longer than the purpose requires. When someone unsubscribes, the
purpose they consented to has ended.

**On unsubscribe the address is replaced by a keyed one-way digest. The row survives; the
address does not.** The project keeps its records and can report on the list, and the person's
address is no longer held.

Three properties this must have, because the obvious implementation does not deliver what the
decision intends:

1. **The digest is keyed, not bare.** `HMAC-SHA256(pepper, normalised_address)`. A plain hash
   of an email address is *not* anonymisation: the address space is enumerable, so anyone with
   a candidate list can hash it and match. The pepper lives **outside the database** — with the
   other secrets, never in a column, never in a migration — so a database dump alone yields no
   addresses. Rotating it breaks recognition of every existing row, so it is long-lived by
   design.
2. **This is pseudonymisation, and is described as such.** A digest that can still recognise a
   returning address is reversible to anyone holding the key — that is what recognition means.
   Calling it anonymous would be wrong, and the distinction decides whether a subject access
   request can still reach these rows. **Suppression and full anonymisation are mutually
   exclusive**; this project has chosen suppression.
3. **It makes an unsubscribe durable.** Because a returning address is still recognisable,
   "leave me alone" survives a third party retyping the address into the signup form. Without
   it, an unsubscribe means only "removed until someone types this again".

**Statistics are computed from the row, not the address** — subscribed, confirmed and
unsubscribed dates, tenure, digests received. Nothing in the reporting needs the address.

Retention of the *pseudonymised* row, and expiry of addresses that were never confirmed, are
still open and belong to the Law group. Storage limitation does not stop at
pseudonymisation, so both are configuration rather than constants, with no default that
quietly becomes policy.

### 2.13 Selection is a word search, and a match is a public label

> *Added 17 August 2026. Decision by the project owner, following the first full run.*

**A case is in the tracker because a curated term appears in it. That is the whole rule.**
There is no score, no threshold and no weighting.

The weighted design that preceded this was not wrong in principle, but in practice it earned
its keep by letting terms stay in the lists that could never have carried a case: `werkzame
stof`, `omwonenden`, `bufferzone`, `NVWA`, `EFSA`, `Wet op de economische delicten`. Held
below the threshold individually, they combined freely, and the first full run over both
corpora selected thousands of judgments on nothing more than a crop name beside an exposure
word. Precision now costs a curation decision instead of an arithmetic one: the term comes
out of the list and into `data/keywords/excluded_<code>.json`, **with the reason it went**.

One instrument survives from the weighted design and is load-bearing without it. `requires`
gates a term on another having matched, which is what lets an active substance whose ISO
common name is an ordinary word — `water`, `beer`, `talc`, `koper` — stay in the list without
admitting every judgment that says it. A gated term whose gate stayed shut selects nothing.

**Every match is a label, and labels are public.** A case carries the curated **term** and
its **category** for each term that selected it. They are shown on the case page, above the
text, and the case list is filtered by both. Three consequences:

- The label is the term **as the curator wrote it**, never the inflection found in the
  judgment, so every spelling of a substance files under one name.
- An alias is a spelling of *its own term* and nothing else. A different substance filed as
  an alias would label the case with the wrong chemical, which is why the twelve bundles that
  did exactly that were split into terms of their own.
- A term is now read by the public, so it is written to be read.

This makes §2.8's explainability requirement something a reader can exercise rather than
something the project asserts: the answer to "why is this case here" is on the case's page.

**What it cost.** The Dutch list went from 877 terms to 863 and the EU list from 573 to 565;
the removals are 17 and 16 terms respectively, offset by product classes split out of
aliases. Almost the whole of both lists — 830 of 863, and 512 of 565 — is active substances,
which is where the project owner placed the emphasis.

---

## 3. Design and layout

### 3.1 Wireframes

Low-fidelity sketches of page layouts to support visual design in content placement and
user flow — to be discussed. The layout agreed for the first iteration is recorded in §3.3.

### 3.2 Design brief

- LAW group styling.
- Sabin Center for Climate Change Litigation as the reference implementation.

**Styling package (added 3 August 2026).** The PLT is a **Wageningen Law** endeavour, not a
WUR-wide one, and does not run on WUR corporate branding. Styling documentation, asset files
and fonts will be supplied by the Law group later in the project. Until they arrive the
front-end uses a neutral academic placeholder palette and a system font stack, defined once
as theme tokens so the styling package can be dropped in as a single change. Developers
should not derive or approximate corporate branding in the meantime.

### 3.3 First-iteration layout

> *Added 3 August 2026.*

- **Header** — branding plus a menu with: *About Wageningen Law*, *Methodology*, *FAQ*,
  *Contact*.
- **Home page** — title "Pesticide Litigation Tracker (PLT)", with a search bar directly
  below it.
- **Map** — below the search bar. Hovering a country reveals the number of cases held for
  that jurisdiction. An **EU logo placed in the North Sea** is hoverable in the same way;
  the EU is treated as a **separate jurisdiction**, not as an aggregation of its member
  states.
- **Sidebar (right)** — the 20 most recent cases, with a button below leading to an
  **All cases** view on its own page.
- **Storage model** — all litigation worldwide is held in **one database** with a
  jurisdiction dimension (sub-entries per jurisdiction), so that map aggregates resolve in a
  single query and adding a jurisdiction is a data/configuration change.

---

## 4. Tech stack

### 4.1 Frontend

React.js with Tailwind CSS for styling.

### 4.2 Backend

Python, using Flask for back-end features.

### 4.3 Database

An SQL relational database. SQLite during development; the schema stays
PostgreSQL-compatible for deployment.

### 4.4 Scheduling

A scheduled job runs the ingestion pipeline weekly per jurisdiction, scanning for new case
law and deduplicating against the existing database (§2.6).

---

## 5. Funding options

1. **EWUU** — [Seed call for preventive health transitions: building the future of preventive health](https://preventivehealth.ewuu.nl/2026/06/seed-call-for-preventive-healthtransitions-building-the-future-of-preventive-health/)

---

## Annex 1: project timeline

| Period | Phase | Activities and outcomes |
| --- | --- | --- |
| June – September 2026 | Project startup | 1. Composition of a core document with detailed descriptions of the project and deliverables<br>2. Development of a first iteration of the database and data pipeline, with basic functionalities for EU pesticide litigation<br>3. First visual impression of website design<br>4. Writing first textual materials as part of the website: about us, instructions, etc. |
| October – November 2026 | Phase A | 1. Development of database and back-end, functional for EU pesticide litigation; version 1.0<br>2. Development of front-end with basic functionality; version 1.0<br>3. Compile a draft version of the website design document for the professional partner |
| December 2026 | Test phase 1 | 1. Deliberation with stakeholders, collect feedback on user experience and design |
| December 2026 – February 2027 | Phase B | 1. Continue back-end development of version 1.0, add functionality for Dutch pesticide litigation and provide API access to database; version 2.0<br>2. Continue front-end development; implement visual design and improvement of functionality; version 2.0<br>3. Contract a professional party for final development and hosting<br>4. Continue development of the website design document for the professional partner |
| March 2027 | Test phase 2 | 1. Deliberation within the project group, collect feedback on user experience |
| March – May 2027 | Phase C | 1. Continue back-end development of version 2.0, add functionality for (available) EU member state litigation; version 3.0<br>2. Improvement of version 2.0 front-end development, full functionality; version 3.0<br>3. Prepare version 3.0 (beta version) for third-party, professional development<br>4. Finalize the website design document for the professional partner |
| June – August 2027 | Third-party development | Beta version delivery to professional development and hosting partner; testing and preparing |
| September 2027 | Launch | |

**Note on sequencing (3 August 2026):** the first build takes the Netherlands and the EU
together, rather than EU-only. The Dutch work scheduled for Phase B is therefore brought
forward into the startup phase.

---

## Annex 2: project data sources

| Jurisdiction | Domain | Court | URL |
| --- | --- | --- | --- |
| EU | EU | Court of Justice of the European Union (CJEU) | https://curia.europa.eu |
| EU | EU | EUR-Lex, and the Publications Office's CELLAR repository behind it | https://eur-lex.europa.eu |
| EU | EU | European e-Justice Portal | https://e-justice.europa.eu |
| Austria | Administrative | Verwaltungsgerichtshof (VwGH) | https://www.vwgh.gv.at |
| Austria | Civil/Criminal | Oberster Gerichtshof (OGH) | https://www.ogh.gv.at |
| Austria | Unified | RIS Legal Information System | https://ris.bka.gv.at |
| Belgium | Administrative | Raad van State / Conseil d'État | https://www.raadvst-consetat.be |
| Belgium | Civil/Criminal | Court of Cassation | https://justitie.belgium.be |
| Belgium | Unified | JUportal | https://juportal.be |
| Bulgaria | Administrative | Supreme Administrative Court | https://www.sac.government.bg |
| Bulgaria | Civil/Criminal | Supreme Court of Cassation | https://www.vks.bg |
| Bulgaria | Constitutional | Constitutional Court | https://www.constcourt.bg |
| Croatia | Administrative | High Administrative Court | https://sudskapraksa.vsrh.hr |
| Croatia | Civil/Criminal | Supreme Court | https://www.vsrh.hr |
| Cyprus | Administrative | Administrative Court | https://www.cylaw.org |
| Cyprus | Civil/Criminal | Supreme Court | https://www.supremecourt.gov.cy |
| Czechia | Administrative | Supreme Administrative Court | https://www.nssoud.cz |
| Czechia | Civil/Criminal | Supreme Court | https://www.nsoud.cz |
| Czechia | Constitutional | Constitutional Court (NALUS) | https://nalus.usoud.cz |
| Denmark | Unified | Danish Courts (Domstolene) | https://domstol.dk |
| Estonia | Unified | Supreme Court (Riigikohus) | https://www.riigikohus.ee |
| Finland | Administrative | Supreme Administrative Court | https://kho.fi |
| Finland | Civil/Criminal | Supreme Court | https://korkeinoikeus.fi |
| France | Administrative | Conseil d'État | https://www.conseil-etat.fr |
| France | Civil/Criminal | Court of Cassation | https://www.courdecassation.fr |
| France | Unified | Légifrance Case Law Database | https://www.legifrance.gouv.fr |
| Germany | Administrative | Federal Administrative Court (BVerwG) | https://www.bverwg.de |
| Germany | Civil/Criminal | Federal Court of Justice (BGH) | https://www.bundesgerichtshof.de |
| Germany | Constitutional | Federal Constitutional Court | https://www.bundesverfassungsgericht.de |
| Greece | Administrative | Council of State (StE) | https://www.ste.gr |
| Greece | Civil/Criminal | Supreme Civil & Criminal Court (Areios Pagos) | https://www.areiospagos.gr |
| Hungary | Unified | Curia of Hungary | https://kuria-birosag.hu |
| Hungary | Constitutional | Constitutional Court | https://alkotmanybirosag.hu |
| Ireland | Unified | Courts Service of Ireland | https://www.courts.ie |
| Italy | Administrative | Council of State (Consiglio di Stato) | https://www.giustizia-amministrativa.it |
| Italy | Civil/Criminal | Supreme Court of Cassation | https://www.cortedicassazione.it |
| Latvia | Unified | Supreme Court of Latvia | https://www.at.gov.lv |
| Lithuania | Administrative | Supreme Administrative Court | https://www.lvat.lt |
| Lithuania | Civil/Criminal | Supreme Court | https://www.lat.lt |
| Luxembourg | Unified | Justice Portal Luxembourg | https://justice.public.lu |
| Malta | Unified | Judiciary of Malta | https://judiciary.mt |
| Netherlands | Administrative | Council of State (Raad van State) | https://www.raadvanstate.nl |
| Netherlands | Administrative (economic) | Trade and Industry Appeals Tribunal (College van Beroep voor het bedrijfsleven, CBb) — appeal forum for authorisation decisions of the Ctgb | https://www.rechtspraak.nl/Organisatie-en-contact/Organisatie/College-van-Beroep-voor-het-bedrijfsleven |
| Netherlands | Civil/Criminal | Supreme Court (Hoge Raad) | https://www.hogeraad.nl |
| Netherlands | Unified | Rechtspraak.nl open data portal — all instances including first instance, and the courts of the Caribbean parts of the Kingdom | https://uitspraken.rechtspraak.nl |
| Poland | Administrative | Supreme Administrative Court (NSA) | https://www.nsa.gov.pl |
| Poland | Civil/Criminal | Supreme Court | https://www.sn.pl |
| Portugal | Administrative | Supreme Administrative Court | https://www.stap.pt |
| Portugal | Civil/Criminal | Supreme Court of Justice | https://www.stj.pt |
| Romania | Unified | High Court of Cassation and Justice | https://www.iccj.ro |
| Slovakia | Unified | Supreme Court / Supreme Administrative Court | https://www.nsud.sk |
| Slovenia | Unified | Judicial Portal of Slovenia | https://www.sodisce.si |
| Spain | Unified | General Council of the Judiciary (Poder Judicial) | https://www.poderjudicial.es |
| Sweden | Administrative | Supreme Administrative Court (Högsta förvaltningsdomstolen) | https://www.domstol.se/hogsta-forvaltningsdomstolen/ |
| Sweden | Civil/Criminal | Supreme Court (Högsta domstolen) | https://www.domstol.se/hogsta-domstolen/ |
| Sweden | Environmental | Land and Environment Courts / Court of Appeal (Mark- och miljödomstolarna, Mark- och miljööverdomstolen) | https://www.domstol.se/hitta-domstol/mark--och-miljodomstolar/ |
| Sweden | Unified | Swedish Courts (Sveriges Domstolar) | https://www.domstol.se |

> **This annex is a starting point per member state, not a map of where pesticide cases are
> heard.** It lists apex courts almost exclusively, and most pesticide litigation never
> reaches one. Sweden shows what that omits: the Land and Environment Courts (*mark- och
> miljödomstolarna*), five specialised divisions of district courts with the Land and
> Environment Court of Appeal above them, hear environmental permitting and environmental
> damage cases, and that is where Swedish pesticide litigation is most likely to sit. Other
> member states have comparable specialised environmental or agricultural jurisdictions that
> this annex does not yet capture. Establishing where the litigation actually is, per
> jurisdiction, is the work §2.9 requires.

### Annex 2a: machine-readable access routes (verified 3 August 2026)

The table above lists publication websites. The endpoints actually used by the pipeline are
recorded here as each jurisdiction is onboarded.

| Jurisdiction | Endpoint | Notes |
| --- | --- | --- |
| NL | `https://data.rechtspraak.nl/uitspraken/zoeken` | Atom feed. Parameters include `max` (≤1000), `from` (offset), `date` (repeatable, from/to), `modified`, `subject` (rechtsgebied URI), `creator` (instantie URI), `type`, `sort`, and `return=DOC`. **`DOC` is the only accepted value of `return`** — `META`, `ALL` and anything else give HTTP 400 (verified 4 August 2026). Omitting `return` yields **all** ECLIs, including metadata-only records with no document body; `return=DOC` yields only those with a body. The difference is large: the portal publishes **3,737,898 decisions, of which 949,461 carry a document body** (verified 5 August 2026). A connector must decide deliberately which it wants: metadata-only records cannot be keyword-filtered on full text (§2.5), but omitting them from discovery means never seeing them. **`modified` is read in Europe/Amsterdam local time while the feed's Atom `updated` is UTC**, and an explicit offset in the parameter is ignored, so a caller passing a UTC instant silently asks for a window one or two hours off the one it meant; a single `modified` value is a lower bound, so a bounded window sends two. **No full-text search** — topical selection must happen client-side (§2.5). |
| NL | `https://data.rechtspraak.nl/uitspraken/content?id=<ECLI>` | Rechtspraak XML: Dublin Core metadata block, `inhoudsindicatie` (abstract) and `uitspraak` (full text). |
| NL | `https://data.rechtspraak.nl/Waardelijst/{Rechtsgebieden,Instanties,Proceduresoorten}` | Controlled vocabularies; seed the reference tables from these rather than hard-coding. |
| EU | `https://publications.europa.eu/webapi/rdf/sparql` | CELLAR SPARQL 1.1 endpoint over the CDM ontology. Used to enumerate case law works by CELEX sector 6 and by document date. From 1 January 2026 a single search returns at most 10,000 results — page by date window. |
| EU | `http://publications.europa.eu/resource/celex/<CELEX>` | CELLAR REST. `Accept: application/xml;notice=object` returns the full metadata notice; `Accept: application/xhtml+xml` with an `Accept-Language` (ISO 639-3, e.g. `eng`) returns the language manifestation of the full text. Expect a 303 to the cellar URI. |
| EU | EUR-Lex SOAP webservice | Alternative to SPARQL, supports full-text queries, but **requires registered credentials** and cannot return document files. Kept as a fallback, not the primary route. |

> **Three details of the CELLAR REST route** (verified 4 August 2026). Each of them costs
> documents rather than merely style:
>
> - **`Accept: text/html` is answered with a 404** for most judgments; the same document is
>   served as `application/xhtml+xml`. Older judgments do come back as `text/html`, so the
>   connector offers both and parses whichever arrives.
> - **The CELEX number must be percent-encoded into the path.** A corrigendum or a second
>   order in a case carries a parenthesised suffix — `62021TO0601(01)` — and CELLAR 404s the
>   unencoded form while serving `62021TO0601%2801%29`.
> - **`cdm:resource_legal_id_sector` is typed `xsd:string`.** A plain `"6"` in a SPARQL
>   filter matches nothing, silently: the query succeeds and returns an empty result set,
>   which is indistinguishable from a quiet week.
>
> Two further facts. CELLAR holds **104,087 distinct case-law CELEX numbers** (verified
> 5 August 2026), and a single CELEX resolves to several cellar works and to one expression
> per language, so the enumeration groups by CELEX and the language versions become documents
> of one case. And **not every decision has
> a retrievable full text**: some carry only a metadata notice, in any language and any
> format, so the pipeline stores the notice and moves on rather than treating it as a
> failure.
