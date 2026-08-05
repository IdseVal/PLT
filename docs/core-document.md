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
2. Terms are **weighted**. Unambiguous terms (active substances, statutory terms) qualify a
   document on their own; contextual terms (crops, exposure, drift) only qualify in
   combination, above a configurable score threshold.
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

1. **Thresholds are not raised to buy precision.** The first EU dry run (1,548 CJEU decisions
   across 2024) selected 54 cases, distributed as `≥12: 18 | 6–12: 8 | 4–6: 7 | 3.0–3.9: 21`.
   Raising `min_score` from 3 to 6 would remove everything below 6 — **28 of the 54**. Raising
   it only to 4 would remove 21, of which roughly two were genuinely in scope; the seven in the
   4–6 band were never separately assessed, so the cost of the 3→6 move is *at least* two
   genuine cases and possibly more. That trade was declined.

   Any future proposal to tighten selection must be assessed the same way: **what does it
   lose**, counted from the distribution rather than estimated. This paragraph originally said
   the 3→6 move cost 21 cases; that was the cost of moving to 4, recomputed by hand from the
   band table and wrong in the direction that made the trade look cheaper than it was.
2. **Precision is handled downstream, by review.** Cases that pass but score near the
   threshold are ingested and published as normal, and additionally flagged for a content
   manager to confirm or reject. Selection admits; review curates.

This is supported by the shape of the evidence rather than assumed: in that run the false
positives were not spread across the score range but concentrated immediately above
`min_score`, while the high-scoring band was almost entirely genuine. A borderline flag
therefore targets the affected population without touching the rest.

**The content manager may be a person or an agent.** That is deliberately undecided, so the
review queue must not assume either — the same queue, record and audit trail has to serve
both.

### 2.8 Methodology must be transparent, explainable and repeatable

> *Added 4 August 2026. Standing constraint on all selection and classification work.*

The PLT publishes its methodology (see the site's Methodology page) because a research
database that cannot account for its own contents is not usable as a source. Three
requirements follow, and they bound what the filter chain may become:

- **Transparent.** How a case was selected is public, not internal. The criteria, the term
  lists and the thresholds are published artefacts.
- **Explainable.** For any individual case it must be possible to say *why it is in the
  database* — which terms matched, in which field, with what weight, against which version
  of which list. This is what `keyword_match` records, and why it is not optional.
- **Repeatable.** Re-running the same selection over the same corpus with the same list
  version must produce the same result. Lists are versioned data in git; scores are
  deterministic; every run is recorded in `ingest_run`.

**A technique that cannot meet all three is out of scope, however well it performs.** This
applies directly to the "later stages" contemplated in §2.5: a classifier that improves
precision but cannot explain an individual verdict, or cannot be re-run to the same answer,
does not qualify for this pipeline as it stands. If such a stage is ever wanted, the
requirement above has to be revisited deliberately, not worked around.

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
| EU | EU | EUR-Lex (EU case law database) | https://eur-lex.europa.eu |
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
| Netherlands | Civil/Criminal | Supreme Court (Hoge Raad) | https://www.hogeraad.nl |
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

> **Sweden added 4 August 2026.** Sweden was absent from the original Annex 2 while every
> other EU member state was listed. The omission surfaced when the map (issue #13) drew
> exactly the coverage this annex states and came out one member state short. Since §1.1
> scopes the PLT to "the EU and its member states", this was a gap rather than a scope
> decision.
>
> Sweden also introduces a **court type no other row in this annex uses**: the Land and
> Environment Courts (*mark- och miljödomstolarna*), five specialised divisions of district
> courts with the Land and Environment Court of Appeal above them, which hear environmental
> permitting and environmental damage cases. That is where Swedish pesticide litigation is
> most likely to sit — not in the supreme courts. Other member states have comparable
> specialised environmental or agricultural jurisdictions that this annex does not yet
> capture, so this annex should be treated as a starting point per member state rather than
> a complete map of where pesticide cases are heard.

### Annex 2a: machine-readable access routes (verified 3 August 2026)

The table above lists publication websites. The endpoints actually used by the pipeline are
recorded here as each jurisdiction is onboarded.

| Jurisdiction | Endpoint | Notes |
| --- | --- | --- |
| NL | `https://data.rechtspraak.nl/uitspraken/zoeken` | Atom feed. Parameters include `max` (≤1000), `from` (offset), `date` (repeatable, from/to), `modified`, `subject` (rechtsgebied URI), `creator` (instantie URI), `type`, `return=DOC\|META`, `sort`. **No full-text search** — topical selection must happen client-side (§2.5). |
| NL | `https://data.rechtspraak.nl/uitspraken/content?id=<ECLI>` | Rechtspraak XML: Dublin Core metadata block, `inhoudsindicatie` (abstract) and `uitspraak` (full text). |
| NL | `https://data.rechtspraak.nl/Waardelijst/{Rechtsgebieden,Instanties,Proceduresoorten}` | Controlled vocabularies; seed the reference tables from these rather than hard-coding. |
| EU | `https://publications.europa.eu/webapi/rdf/sparql` | CELLAR SPARQL 1.1 endpoint over the CDM ontology. Used to enumerate case law works by CELEX sector 6 and by document date. From 1 January 2026 a single search returns at most 10,000 results — page by date window. |
| EU | `http://publications.europa.eu/resource/celex/<CELEX>` | CELLAR REST. `Accept: application/xml;notice=object` returns the full metadata notice; `Accept: text/html` with `Accept-Language` returns the language manifestation of the full text. |
| EU | EUR-Lex SOAP webservice | Alternative to SPARQL, supports full-text queries, but **requires registered credentials** and cannot return document files. Kept as a fallback, not the primary route. |
