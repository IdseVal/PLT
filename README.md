# Pesticide Litigation Tracker (PLT)

An open-access, automatically updated database of pesticide-related case law from the
European Union and its member states, built by the **Law group of Wageningen University
& Research**.

The PLT is modelled on the [Sabin Center's Climate Change Litigation Databases](https://climatecasechart.com/):
a searchable, map-driven front door onto a curated body of case law, kept current by an
automated ingestion pipeline rather than by manual entry.

> **This README is the maintained project overview.** The full project blueprint —
> goals, functional requirements, user roles, timeline and data sources — lives in
> [`docs/core-document.md`](docs/core-document.md) and is the living successor to the
> original `Core document.pdf`. Update the Markdown, not the PDF.

---

## 1. What the PLT is

Within the project's scope, **pesticide-related cases** are public, private and criminal
law cases that centre on the effects, governance and/or liability of pesticide
admission, trade and/or use.

The PLT collects those cases, stores them with as much source metadata as the publishing
court exposes, classifies them, and publishes them in their original language and full
text with a link back to the original publication.

**Target audience:** scholars and researchers, legal professionals, NGOs, civil servants,
and anyone following legal developments in pesticide governance. Open access, to serve
academic research and civil-society use.

## 2. What the PLT does

### Public site

| Area | Behaviour |
| --- | --- |
| **Header** | Wageningen Law branding, plus a menu: *About Wageningen Law*, *Methodology*, *FAQ*, *Contact* |
| **Home** | Title "Pesticide Litigation Tracker (PLT)" with a prominent search bar directly below |
| **Map** | Below the search bar. Hovering a country shows how many cases the database holds for that jurisdiction. An EU logo sits in the North Sea and is hoverable in the same way — the **EU is treated as its own jurisdiction**, not as an aggregate of its member states |
| **Sidebar** | The 20 most recent cases, with a button through to a dedicated **All cases** page |
| **All cases** | Full listing with the classification filters from §2.2 of the core document, pagination and download |
| **Case detail** | Full text in the original language, abstract, classification metadata, and a deep link to the source |
| **Email alerts** | A signup at the end of the home page's right-hand column: one email a week listing the newly found cases. No account and no login — an address is confirmed by a link before anything is sent to it, and every message carries a one-click way out |

### Behind the site

1. **Fetching pipeline** — per-jurisdiction connectors pull case law from official open-data
   endpoints, apply a filter chain, and write matches to the database.
2. **Linguistic filtering (filter stage 1)** — because none of the source APIs offer a usable
   topical filter for "pesticides", candidate documents are matched against a curated,
   per-jurisdiction, per-language keyword list. See [§4](#4-keyword-filters).
   The filter chain is deliberately pluggable so later stages (classifier models,
   citation-based filters, manual curation) can be added without touching the connectors.
3. **Deduplication** — every run checks incoming documents against what the database already
   holds, keyed on the source identifier (ECLI / CELEX), so re-runs never create duplicates.
4. **Weekly scan** — a scheduled job re-runs the pipeline for each jurisdiction, fetching only
   what has changed since the last successful checkpoint.
5. **Notifications** — the scan tells an administrator which cases it flagged for review, and
   a second scheduled job emails the subscriber list a digest of what the scan added. Both
   send through the standard library; a development checkout writes messages to a log or a
   file and cannot mail a real address.

## 3. Jurisdictions and data sources

All litigation worldwide is stored in **one database**, with a `jurisdiction` dimension so
each jurisdiction is a sub-entry rather than a separate store. This keeps the map query a
single aggregate, and keeps adding a jurisdiction a data/config exercise.

Launch jurisdictions:

| Jurisdiction | Source | Access route |
| --- | --- | --- |
| **Netherlands** (`NL`) | Rechtspraak.nl Open Data | `https://data.rechtspraak.nl/uitspraken/zoeken` (Atom) + `/uitspraken/content?id=<ECLI>` (Rechtspraak XML with Dublin Core metadata) |
| **European Union** (`EU`) | EUR-Lex / CELLAR | CELLAR SPARQL endpoint `https://publications.europa.eu/webapi/rdf/sparql` for enumeration + CELLAR REST `http://publications.europa.eu/resource/celex/<CELEX>` for notices and full text |

The full member-state source table is in [Annex 2 of the core document](docs/core-document.md#annex-2-project-data-sources).

## 4. Keyword filters

Keyword lists live in [`data/keywords/`](data/keywords/), one JSON file per jurisdiction,
validated against [`data/keywords/schema.json`](data/keywords/schema.json).

**Every jurisdiction added to the database needs its own list**, written in the working
language(s) of that jurisdiction's courts — a Dutch list will not find German cases. This
is a standing precondition for onboarding a jurisdiction, recorded in
[§2.5 of the core document](docs/core-document.md#25-linguistic-filtering-and-per-jurisdiction-keyword-lists).

Terms are weighted so that unambiguous terms (`glyfosaat`, `gewasbeschermingsmiddel`)
qualify a document on their own, while contextual terms (`lelieteelt`, `spuitzone`,
`omwonenden`) only qualify in combination. Curation of these lists is a **content manager**
responsibility, not a developer one.

## 5. Tech stack

- **Frontend** — React + Tailwind CSS
- **Backend** — Python + Flask
- **Database** — SQL relational database (SQLite for development, PostgreSQL-compatible schema)
- **Scheduling** — weekly automated pipeline run, followed by the subscriber digest
- **Email** — the Python standard library's `smtplib`; no third-party email service

## 6. Repository layout

```
backend/     Flask API, SQLAlchemy models, migrations, fetching pipeline
frontend/    React + Tailwind single-page application
data/        Keyword filter lists and other curated reference data
docs/        Core document and supporting documentation
```

## 7. Styling

The PLT is a **Wageningen Law** project. Styling documentation, asset files and fonts will
be supplied by the Law group; until they arrive the front-end runs on a neutral academic
placeholder palette and a system font stack, defined once as Tailwind theme tokens so the
styling package drops in as a single change. Do not derive or approximate WUR corporate
branding in the meantime — see [core document §3.2](docs/core-document.md#32-design-brief).

## 8. Project status

Startup phase (June–September 2026): first iteration of the database and data pipeline,
first visual impression of the website. See the
[project timeline](docs/core-document.md#annex-1-project-timeline) for the full schedule
through launch in September 2027.

## 9. Credits

Law group, Wageningen University & Research — Edwin Alblas, Idse Val & Vincent Latjes.
