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
- The subject-matter classification a connector reads — the *rechtsgebied* for the
  Netherlands — has no column here and is stored as `case.source_metadata["subject"]`. It is
  a **scored** field of the filter chain nonetheless; see §4.2.

**Repository helpers** (`plt/db/repositories.py`) are the only SQL the API layer calls:
`search_cases` / `count_cases` / `stream_cases` (all taking a `CaseSearchCriteria`),
`latest_cases`, `get_case_by_source_id`, `get_case_by_id`, `get_case_fingerprint` (the
pipeline's dedup pre-check), `jurisdiction_stats` (the one-query map payload, EU included and
zero-case jurisdictions retained), `list_facets` and `latest_successful_runs`. Clamping
`page`, `page_size` and `limit` against `Settings` stays the caller's job.

---

## 4. Pipeline interfaces

Onboarding a jurisdiction is **one connector class and one keyword list, and nothing else**.
Nothing under `plt/pipeline/` outside `connectors/` may name a jurisdiction, and the registry
discovers connector modules rather than listing them, so there is no third file to forget.

### 4.1 The connector

```python
class SourceConnector(ABC):
    """One jurisdiction's data source."""

    jurisdiction_code: ClassVar[str]     # "NL"; the registry keys on it
    name: ClassVar[str]                  # "rechtspraak"; ingest_run.connector and the
                                         # ingest_checkpoint primary key

    def __init__(self, settings: Settings | None = None) -> None: ...

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

    def close(self) -> None: ...          # called in a finally, interruptions included
```

Drop the module into `plt/pipeline/connectors/`; `plt.pipeline.registry` imports it, reads
the two class attributes and builds it with `cls(settings)`. Two connectors claiming one
jurisdiction is an error, not last-one-wins.

**Fetch through `plt.pipeline.http.PoliteClient`.** It applies the configured request rate,
exponential backoff with jitter on 429/5xx, the descriptive `User-Agent`, and abandons a
backoff as soon as the run is asked to stop. A connector composing `httpx` calls itself
silently loses all four. An explicit `Retry-After` is obeyed **as the source sent it** and is
never shortened to the jitter ceiling; a pause longer than `http_retry_after_max_seconds`
ends the run instead, leaving the checkpoint for the next scheduled one.

**Errors.** `DocumentUnavailableError` scopes a failure to one document: the runner logs it,
counts it, holds the checkpoint back and carries on. `SourceUnavailableError` ends the run
without advancing the checkpoint. Anything else raised per document is treated as the former,
with a stack trace.

### 4.2 What crosses the stages

| Type | Purpose | Notes |
| --- | --- | --- |
| `Candidate` | What discovery found | `source_id`, `jurisdiction_code`, `modified_at` (drives the checkpoint), optional `content_hash`, `cursor`, `title`, `source_url`, `source_metadata` |
| `RawDocument` | The response verbatim | `payload`, `media_format`, `retrieved_at`; stored as `case_document.raw_payload` (rule 2.6) |
| `NormalisedCase` | Section 3, in memory | Every named column, plus `subject`, `court`, `documents`, `parties`, `citations`, `source_metadata` |
| `NormalisedDocument` | One `case_document` row | `doc_type`, `language`, `full_text`, `raw_payload`, `media_format` |

Two properties of `NormalisedCase` matter to every connector:

- **`full_text` is computed.** The schema keeps full texts on `case_document`, one row per
  language, so a case may carry several, while a filter stage wants one text to scan.
  `NormalisedCase.full_text` joins the text-bearing documents, the case's own `language`
  first and the rest in a stable order. A term in any language version therefore qualifies
  the case, which is what a multilingual jurisdiction needs; a case with a single document
  passes that document's text through by reference rather than copying it. Consequently the
  members of `FilterableDocument` are declared **read-only**: a stage only reads them, and a
  settable-attribute protocol would reject a computed one.
- **`subject` is scored.** The *rechtsgebied* for the Netherlands, the subject-matter heading
  for the EU. Both shipped keyword lists weight it — the EU list at 1.2, above plain full
  text — so a connector that leaves it `None` throws away a strong signal. It has no column
  of its own in section 3 and is persisted under `case.source_metadata["subject"]`.

**Content hash.** `case.content_hash` is resolved in this order: the hash the connector set
on the `NormalisedCase`; otherwise the one discovery put on the `Candidate`; otherwise a
SHA-256 fingerprint of the normalised content. A connector that exposes a cheap revision
marker at discovery (an Atom `updated`, a CELLAR modification date) should put it on the
`Candidate` and leave `NormalisedCase.content_hash` unset — the two then live in one hash
space, which is what lets the pre-check skip a document **without fetching it**.

### 4.3 The filter chain

```python
class Filter(ABC):
    """A stage in the filter chain."""

    @abstractmethod
    def evaluate(self, case: FilterableDocument) -> FilterResult:
        """FilterResult carries passed: bool, score: float, matches: list[TermMatch],
        and a human-readable reason for the pipeline report."""
```

`FilterableDocument` is a structural protocol over `jurisdiction_code`, `title`, `abstract`,
`subject` and `full_text`, so no import couples a stage to the connector work stream. Stage 1
is the keyword matcher; a later stage appends to the `FilterChain` and touches no connector.

### 4.4 The runner

```python
run_jurisdiction(code, since=None, until=None, dry_run=False, *, connector=None, chain=None,
                 settings=None, session_factory=None, batch_size=None,
                 report_path=None) -> IngestReport
```

Order per jurisdiction: `discover → dedup pre-check (skip known unchanged) → fetch →
normalise → filter chain → persist → checkpoint`. The pre-check exists so unchanged documents
are never re-fetched. The keyword-only arguments are injection points for the CLI and for
tests; the positional signature is the contract.

- **Streaming.** `discover` is consumed lazily and sliced into `pipeline_batch_size` batches,
  each committed in its own session. Peak memory is one batch plus the document in hand.
- **Per-document isolation.** Each document runs inside a savepoint, so a failure rolls back
  that document alone and the batch keeps its other work.
- **Interruption.** `SIGINT`/`SIGTERM` set a flag: the document in flight finishes, its batch
  commits, the checkpoint records exactly what was committed, and the run row closes as
  `interrupted`. A second signal falls through to the default handler.
- **Checkpoint safety.** The position advances only over documents that were processed
  successfully *and* committed, and stops advancing at the first failure of the run, so
  nothing after a failed document is ever considered done. A failed run writes no checkpoint.
  The lower bound of a window is inclusive; deduplication absorbs the overlap.
- **`dry_run`** runs every stage and writes the match report, and makes **no database
  changes**. It still *reads* the database, because the deduplication pre-check has to.
- **Reporting.** Every non-dry run writes an `ingest_run` row with accurate
  fetched/matched/inserted/updated/skipped/error counts, the checkpoint before and after, and
  a status of `success`, `partial`, `failed` or `interrupted`. The same numbers come back as
  an `IngestReport`. A failed run is reported through that status rather than by raising, so
  `--all` continues to the next jurisdiction.

The match report is JSON Lines, one object per document judged — accepted *and* rejected,
because a recall problem is invisible in a report that lists only successes — written to
`PLT_PIPELINE_REPORT_DIR` and flushed per line.

### 4.5 The CLI

```
plt ingest --jurisdiction NL [--since ...] [--until ...] [--dry-run] [--report PATH]
plt ingest --all
```

Timestamps are parsed as UTC. Exit codes: `0` completed, `1` failed, `130` interrupted.

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
