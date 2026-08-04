# PLT architecture contract

This document is the **integration contract** between work streams. Several agents build
against it in parallel; anything crossing a module boundary is fixed here so the pieces fit
on merge. Changing a contract in this file means updating this file in the same PR and
saying so in the PR description.

Companion documents: [`README.md`](../README.md) (what the app is),
[`docs/core-document.md`](core-document.md) (project blueprint).

---

## 1. Repository layout

```
backend/
  pyproject.toml            Dependency + tool config (ruff, mypy, pytest)
  .env.example              Every environment variable, documented, no secrets
  alembic.ini
  migrations/               Alembic revisions
  plt/
    __init__.py
    config.py               Pydantic settings loaded from environment
    app.py                  Flask application factory: create_app()
    extensions.py           db session, CORS, rate limiter
    db/
      base.py               Declarative base, naming convention
      models.py             SQLAlchemy ORM models (§3)
      session.py            Engine/session lifecycle
      repositories.py       Query helpers used by the API layer
    api/
      __init__.py           Blueprint registration
      cases.py              /api/cases*
      stats.py              /api/stats*
      schemas.py            Request/response (de)serialisation + validation
      errors.py             Uniform error envelope
    pipeline/
      runner.py             Orchestrator: run_jurisdiction(code, since=None)
      base.py               SourceConnector ABC + RawDocument/NormalisedCase dataclasses
      checkpoint.py         Per-connector checkpoint read/write
      dedup.py              Source-identifier and content-hash deduplication
      filters/
        base.py             Filter ABC (the chain is pluggable)
        keywords.py         Stage 1: keyword matcher over data/keywords/*.json
      connectors/
        rechtspraak.py      NL
        eurlex.py           EU
    cli.py                  `flask plt ...` / `python -m plt.cli` entry points
    utils/logging.py        Structured logging setup
  tests/
    unit/  integration/  fixtures/
frontend/
  package.json  vite.config.ts  tailwind.config.js  tsconfig.json
  src/
    main.tsx  App.tsx
    api/client.ts           Typed fetch wrapper, single place the API base URL is read
    components/             Header, SearchBar, LatestCases, MapEurope, CaseCard, ...
    pages/                  Home, AllCases, CaseDetail, About, Methodology, Faq, Contact
    hooks/  types/  styles/
  tests/
data/keywords/              Curated filter lists (schema.json + one file per jurisdiction)
docs/
.github/workflows/          ci.yml, weekly-ingest.yml
```

**Language and tooling:** backend Python 3.11+, fully type-annotated, `ruff` + `mypy` +
`pytest`. Frontend React 18 + TypeScript + Vite + Tailwind, `eslint` + `vitest`. No
`any`, no untyped Python function signatures.

---

## 2. Cross-cutting rules

1. **Configuration is external.** No hard-coded URLs, paths, credentials, page sizes or
   rate limits. Everything through `plt.config.Settings`, documented in `.env.example`.
2. **Interruptible.** Every long-running loop handles `SIGINT`/`KeyboardInterrupt`, finishes
   the item in flight, writes its checkpoint, and exits cleanly.
3. **Incremental, not in-memory.** The pipeline streams page by page and commits per batch.
   Never accumulate a full corpus in memory or in one transaction.
4. **Resumable.** Every connector run writes a checkpoint; a re-run after an interruption
   resumes rather than restarting.
5. **Politeness to sources.** Configurable request rate, retry with exponential backoff and
   jitter on 429/5xx, a descriptive `User-Agent` naming the project and a contact address.
   These are public research endpoints — do not hammer them.
6. **Raw payloads are kept.** Store the source response verbatim alongside the parsed record
   so reclassification never needs a re-fetch.
7. **Logging.** Structured, levelled, no PII from case texts in logs beyond identifiers.
8. **Tests are part of the deliverable.** Network calls are mocked against recorded fixtures
   in unit tests; integration tests hitting live endpoints are marked and opt-in.

---

## 3. Database schema (contract)

SQLite in development, PostgreSQL-compatible. SQLAlchemy 2.0 typed ORM, Alembic migrations.
Timestamps are timezone-aware UTC. Column list is the required minimum — connectors may
add source-specific fields to `source_metadata`.

| Table | Purpose | Key columns |
| --- | --- | --- |
| `jurisdiction` | One row per jurisdiction, EU included as its own row | `code` (PK, `NL`/`EU`), `name`, `type` (`state`\|`supranational`), `iso_alpha2`, `map_feature_id`, `is_active` |
| `court` | Courts/instances, seeded from source vocabularies | `id`, `jurisdiction_code` (FK), `source_identifier` (unique per jurisdiction), `name`, `level`, `domain` |
| `case` | The central entity, one row per decision | `id`, `jurisdiction_code` (FK), `source_id` (**unique with jurisdiction**: ECLI or CELEX), `source_system`, `court_id` (FK), `title`, `abstract`, `decision_date`, `filing_date`, `publication_date`, `case_numbers` (JSON), `language`, `law_domain`, `law_subfield`, `procedure_type`, `outcome`, `source_url`, `content_hash`, `first_seen_at`, `last_seen_at`, `updated_at`, `source_metadata` (JSON), `is_published` |
| `case_document` | Full texts and attachments per case, per language | `id`, `case_id` (FK), `language`, `doc_type` (`judgment`\|`opinion`\|`summary`), `format`, `full_text`, `raw_payload`, `retrieved_at` |
| `party` | Litigating parties | `id`, `case_id` (FK), `name`, `role` (`applicant`\|`defendant`\|`intervener`\|`other`), `party_type` |
| `topic` + `case_topic` | Topic classification (§2.2 label 6), extensible | `id`, `slug`, `label`, `parent_id` |
| `keyword_match` | Which term ids matched a case, and where | `id`, `case_id` (FK), `term_id`, `list_version`, `field`, `weight_applied`, `snippet` |
| `citation` | Instruments and cases cited (CELEX/ECLI) | `id`, `case_id` (FK), `target_identifier`, `citation_type` |
| `ingest_run` | One row per pipeline execution | `id`, `jurisdiction_code`, `connector`, `started_at`, `finished_at`, `status`, `fetched_count`, `matched_count`, `inserted_count`, `updated_count`, `skipped_duplicate_count`, `error_count`, `checkpoint_before`, `checkpoint_after` |
| `ingest_checkpoint` | Resumable position per connector | `connector` (PK), `jurisdiction_code`, `last_modified_seen`, `last_cursor`, `updated_at` |

**Deduplication key:** `UNIQUE (jurisdiction_code, source_id)`. On conflict, compare
`content_hash`; identical → touch `last_seen_at` only; different → update in place and
record the revision (`case.revision` is incremented). Never insert a second row for the same
source identifier.

**`keyword_match` matters:** it is how the content manager evaluates and tunes the keyword
lists. Do not treat it as optional.

**Portability rules, fixed by the implementation of the schema:**

- Timestamps use `plt.db.base.UtcDateTime`, which is `TIMESTAMP WITH TIME ZONE` on
  PostgreSQL and a converting decorator on SQLite. A naive `datetime` is rejected on write.
- Enumerated columns are `VARCHAR` plus a named `CHECK` constraint, never a native
  PostgreSQL `ENUM`, so a new member is an ordinary migration on both back ends. Values are
  persisted as the lower-case enum *value* (`state`, `judgment`, `applicant`, …), which is
  also what the API exposes.
- JSON columns use the portable `JSON` type, not `JSONB`.
- `case_document.doc_type` takes `judgment | opinion | summary | attachment | other`; the
  last two exist so a connector meeting an unexpected document kind stores it rather than
  discarding it.
- `jurisdiction.map_feature_id` is the identifier the frontend map resolves a jurisdiction
  against: the ISO 3166-1 alpha-2 code for a state (`NL`), and the sentinel `EU` for the
  Union, which the map renders as the hoverable North Sea logo instead of a shape.

**Repository helpers** (`plt/db/repositories.py`) are the only SQL the API layer calls:
`search_cases` / `count_cases` / `stream_cases` (all taking a `CaseSearchCriteria`),
`latest_cases`, `get_case_by_source_id`, `get_case_by_id`, `get_case_fingerprint` (the
pipeline's dedup pre-check), `jurisdiction_stats` (the one-query map payload, EU included and
zero-case jurisdictions retained), `list_facets` and `latest_successful_runs`. Clamping
`page`, `page_size` and `limit` against `Settings` stays the caller's job.

---

## 4. Pipeline interfaces

```python
class SourceConnector(ABC):
    """One jurisdiction's data source."""

    jurisdiction_code: str
    name: str

    @abstractmethod
    def discover(self, since: datetime | None, until: datetime | None) -> Iterator[Candidate]:
        """Yield candidate identifiers + light metadata, oldest first, streaming."""

    @abstractmethod
    def fetch(self, candidate: Candidate) -> RawDocument:
        """Retrieve the full document and its raw payload for one candidate."""

    @abstractmethod
    def normalise(self, raw: RawDocument) -> NormalisedCase:
        """Map source fields onto the schema in section 3, preserving everything else
        in source_metadata."""
```

```python
class Filter(ABC):
    """A stage in the filter chain."""

    @abstractmethod
    def evaluate(self, case: NormalisedCase) -> FilterResult:
        """FilterResult carries passed: bool, score: float, matches: list[TermMatch],
        and a human-readable reason for the pipeline report."""
```

Runner order per jurisdiction: `discover → dedup pre-check (skip known unchanged) → fetch →
normalise → filter chain → persist → checkpoint`. The dedup pre-check exists so unchanged
documents are never re-fetched.

`run_jurisdiction(code, since=None, until=None, dry_run=False)` must support `dry_run`,
which runs everything and writes a match report but no database changes.

---

## 5. HTTP API contract

Base path `/api`. JSON only. All list endpoints paginate. Errors use one envelope:
`{"error": {"code": "...", "message": "...", "details": {...}}}`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/cases` | Search + filter + paginate. Query params: `q` (full text), `jurisdiction` (repeatable), `law_domain`, `law_subfield`, `topic`, `court`, `language`, `date_from`, `date_to`, `sort` (`date_desc` default, `date_asc`, `relevance`), `page`, `page_size` (default 20, max 100) |
| `GET` | `/api/cases/latest?limit=20` | Sidebar feed, newest first, `limit` max 50 |
| `GET` | `/api/cases/<jurisdiction>/<source_id>` | Single case with documents, parties, topics, matched terms |
| `GET` | `/api/cases/export` | Same filters as `/api/cases`, plus `format` (`csv` default, or `jsonl`). Not paginated: it streams every match |
| `GET` | `/api/stats/jurisdictions` | Map payload — **one query, all jurisdictions, EU included** |
| `GET` | `/api/filters` | Facet values for the All-cases filter UI |
| `GET` | `/api/health` | Liveness + last successful ingest per jurisdiction |

### 5.1 Response shapes

Fixed here, not left to the implementation: several frontend streams build against the same
JSON. **A field that has no value is present and `null`; it is never omitted**, and no list
endpoint returns a bare array except `/api/stats/jurisdictions`, whose payload *is* the
array below. Dates are `YYYY-MM-DD`; timestamps are ISO 8601 with a UTC offset.

**Paginated envelope** — `/api/cases`:

```json
{ "items": [CaseSummary], "page": 1, "page_size": 20,
  "total": 137, "page_count": 7, "has_next": true }
```

Paging is forgiving on purpose, because a client pages through a corpus that grows and
shrinks underneath it as ingestion runs:

- **A `page` beyond the last one is `200` with an empty `items` array**, not a `404` or a
  `400`. `page`, `total` and `page_count` are still reported, so a client that overshot can
  see it did and step back. Only a `page` below 1 is a validation error.
- **`page_count` is never `0`.** An empty result set is one empty page: `total: 0`,
  `page_count: 1`, `has_next: false`, so a paginator renders "1 of 1" rather than "1 of 0".
- **`sort=relevance` with no `q` falls back to `date_desc`** rather than being rejected:
  there is nothing to rank, and a UI that keeps `sort=relevance` in the URL while the user
  clears the search box must still get results. A blank or whitespace-only `q` is treated
  as no `q` throughout.

**`/api/cases/latest`** is a feed, not a page: `{ "items": [CaseSummary], "limit": 20 }`.

**`CaseSummary`** — one search result or feed entry, everything a card renders:

```json
{ "id": 42,
  "jurisdiction_code": "NL", "jurisdiction_name": "Netherlands",
  "source_id": "ECLI:NL:RVS:2024:1", "source_system": "rechtspraak",
  "court_id": 3, "court_name": "Raad van State",
  "title": "...", "abstract": "...",
  "decision_date": "2024-05-01", "publication_date": "2024-05-03",
  "case_numbers": ["202301234/1/A3"], "language": "nl",
  "law_domain": "public", "law_subfield": "environmental",
  "procedure_type": "appeal", "outcome": "dismissed",
  "source_url": "https://..." }
```

Names are flattened rather than nested, so the JSON, the JSON-Lines export and the CSV
export name a field the same way. `court_id` and `court_name` are both `null` when the
source named no court; `court_id` is carried because the `court` filter takes an id, so a
card can link to the rest of that court's cases without resolving the name first.
`law_domain` is one of `public`, `private`, `criminal`, `other`.

**`CaseDetail`** — `/api/cases/<jurisdiction>/<source_id>`: every `CaseSummary` field, plus
`filing_date`, `revision`, `first_seen_at`, `last_seen_at`, `updated_at` and five lists:

| List | Members |
| --- | --- |
| `documents` | `id`, `language`, `doc_type`, `format`, `full_text`, `source_url`, `byte_size`, `retrieved_at` |
| `parties` | `id`, `name`, `role`, `party_type`, `ordinal` |
| `topics` | `slug`, `label`, `confidence`, `assigned_by` |
| `keyword_matches` | `term_id`, `term`, `list_version`, `field`, `weight_applied`, `match_count`, `snippet` |
| `citations` | `target_identifier`, `target_scheme`, `citation_type`, `target_title`, `target_url` |

`case_document.raw_payload` is **never** exposed: it is the verbatim source response, kept
for reclassification, and can be megabytes of markup. Nor is `case.is_published` — an
unpublished case is reported as a 404, not as a flag a client could read.

**`/api/stats/jurisdictions`** — a bare array, one entry per jurisdiction, ordered by code,
**including jurisdictions whose `case_count` is `0`** so the map renders intended coverage
in its muted state rather than dropping the shape:

```json
[{ "code": "EU", "name": "European Union", "type": "supranational",
   "map_feature_id": "EU", "is_active": true,
   "case_count": 12, "latest_decision_date": "2024-05-01" }]
```

**`/api/filters`** — `jurisdictions: [{code, name}]`, `courts: [{id, name}]`,
`topics: [{slug, label}]`, the string lists `law_domains`, `law_subfields`, `languages`,
`sorts` and `export_formats`, `decision_date_range: {from, to}`, and the bounds the server
enforces (`page_size_default`, `page_size_max`, `latest_limit_max`) so the filter UI reads
them rather than repeating them.

**`/api/health`** — `{ "status": "ok", "service": "plt-api", "version": "0.1.0",
"database": "ok" | "unavailable", "ingest": { "NL": "2026-07-01T09:30:00+00:00" } }`.
`ingest` maps a jurisdiction code onto the finishing timestamp of its last *successful*
run and is empty until the pipeline has completed one. A database that cannot be reached
does not fail the endpoint: liveness is about the process, so it is reported in `database`.

**`/api/cases/export`** — `format=csv` returns a header row of flat columns
(`id`, `jurisdiction_code`, `jurisdiction_name`, `source_id`, `source_system`, `court_name`,
`title`, `abstract`, `decision_date`, `filing_date`, `publication_date`, `case_numbers`,
`language`, `law_domain`, `law_subfield`, `procedure_type`, `outcome`, `source_url`,
`revision`, `first_seen_at`, `last_seen_at`) followed by one row per case. `format=jsonl`
emits a first line describing the export (`record_type: "metadata"`, `generated_at`, the
filters used) and then one `record_type: "case"` object per line carrying the same fields.

### 5.2 Errors and limits

Every failure, including 404, 405 and 429, uses the envelope above. `details` names the
offending parameter for a validation error, e.g.
`{"parameter": "page_size", "value": "5000", "minimum": 1, "maximum": 100}`.

**Security requirements** (not optional, these are public endpoints):
parameterised queries only; validate and bound every query parameter server-side;
`page_size` and `limit` bounded by configuration, and a value outside the bound **rejected
with a 400 rather than silently coerced**; rate limiting on all endpoints (per endpoint,
per client) and a stricter limit on `/api/cases/export`; CORS restricted to configured
origins; no stack traces, SQL or file paths in responses — an unexpected exception logs
server-side and answers with a generic `internal_error` envelope.

---

## 6. Frontend contract

- **Routes:** `/` (home), `/cases` (all cases), `/cases/:jurisdiction/:sourceId` (detail),
  `/about`, `/methodology`, `/faq`, `/contact`.
- **Header** on every route: branding + menu *About Wageningen Law*, *Methodology*, *FAQ*,
  *Contact*.
- **Home:** title "Pesticide Litigation Tracker (PLT)"; search bar directly beneath it; map
  below the search bar; right-hand sidebar with the 20 latest cases and a button to
  `/cases`.
- **Map:** Europe, one hoverable shape per jurisdiction, tooltip showing the case count from
  `/api/stats/jurisdictions`. An **EU logo positioned in the North Sea** is hoverable on the
  same footing and links through to `/cases?jurisdiction=EU`. Jurisdictions with no data
  render in a muted "no cases yet" state rather than disappearing. Keyboard-accessible: the
  hover affordance must have a focus equivalent.
- **Search submits to `/cases?q=…`** — the home search bar does not render results in place.
- All server data flows through `src/api/client.ts`. No component calls `fetch` directly.
- Tailwind, with all colours and type defined once as theme tokens, never as ad-hoc hex
  values in components. Responsive; accessible (labelled controls, visible focus, contrast).
- **Styling is placeholder until the Wageningen Law styling package arrives** — the project
  owner will supply styling documentation, asset files and fonts. Until then: a neutral,
  restrained academic palette and a system font stack, defined in `tailwind.config.js` and
  marked as placeholders. Do not derive, approximate or reverse-engineer WUR corporate
  branding; the PLT is a Wageningen Law project, not a WUR-wide one. Every visual decision
  must be expressible through the theme tokens so the eventual swap is a one-file change.

---

## 7. Scheduling

A weekly job per jurisdiction runs `run_jurisdiction(code)` with no `since`, letting the
stored checkpoint supply the window. Implemented as a GitHub Actions workflow
(`.github/workflows/weekly-ingest.yml`) with `workflow_dispatch` for manual runs, and as a
CLI command so a server cron can call the identical path. A failed run must not advance the
checkpoint.
