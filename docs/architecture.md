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
| `GET` | `/api/cases/export` | Same filters as `/api/cases`, returns CSV or JSON-Lines including metadata |
| `GET` | `/api/stats/jurisdictions` | Map payload: `[{code, name, type, map_feature_id, case_count, latest_decision_date}]` — **one query, all jurisdictions, EU included** |
| `GET` | `/api/filters` | Facet values for the All-cases filter UI |
| `GET` | `/api/health` | Liveness + last successful ingest per jurisdiction |

**Security requirements** (not optional, these are public endpoints):
parameterised queries only; validate and bound every query parameter server-side;
`page_size` and `limit` clamped; rate limiting on all endpoints and a stricter limit on
`/api/cases/export`; CORS restricted to configured origins; no stack traces or SQL in
responses.

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
- Tailwind, with WUR Law group colours defined once as theme tokens, never as ad-hoc hex
  values in components. Responsive; accessible (labelled controls, visible focus, contrast).

---

## 7. Scheduling

A weekly job per jurisdiction runs `run_jurisdiction(code)` with no `since`, letting the
stored checkpoint supply the window. Implemented as a GitHub Actions workflow
(`.github/workflows/weekly-ingest.yml`) with `workflow_dispatch` for manual runs, and as a
CLI command so a server cron can call the identical path. A failed run must not advance the
checkpoint.
