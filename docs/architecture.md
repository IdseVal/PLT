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
      reviews.py            /api/reviews* — the content manager's queue (§2.7), authenticated
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
| `case` | The central entity, one row per decision | `id`, `jurisdiction_code` (FK), `source_id` (**unique with jurisdiction**: ECLI or CELEX), `source_system`, `court_id` (FK), `title`, `abstract`, `decision_date`, `filing_date`, `publication_date`, `case_numbers` (JSON), `language`, `law_domain`, `law_subfield`, `procedure_type`, `outcome`, `source_url`, `content_hash`, `first_seen_at`, `last_seen_at`, `updated_at`, `source_metadata` (JSON), `is_published`, `filter_score`, `needs_review` |
| `case_document` | Full texts and attachments per case, per language | `id`, `case_id` (FK), `language`, `doc_type` (`judgment`\|`opinion`\|`summary`), `format`, `full_text`, `raw_payload`, `retrieved_at` |
| `party` | Litigating parties | `id`, `case_id` (FK), `name`, `role` (`applicant`\|`defendant`\|`intervener`\|`other`), `party_type` |
| `topic` + `case_topic` | Topic classification (§2.2 label 6), extensible | `id`, `slug`, `label`, `parent_id` |
| `keyword_match` | Which term ids matched a case, and where | `id`, `case_id` (FK), `term_id`, `list_version`, `field`, `weight_applied`, `snippet` |
| `case_review` | One row per case flagged for review, and the standing decision on it | `id`, `case_id` (**unique**, FK), `status` (`pending`\|`confirmed`\|`rejected`\|`withdrawn`), `score`, `min_score`, `band_ceiling`, `list_version`, `reason`, `flagged_at`, `flagged_revision`, `flagged_content_hash`, `decision` (`confirmed`\|`rejected`), `decided_by`, `decided_at`, `decided_revision`, `decision_note`, `suppressed_publication` |
| `case_review_decision` | Append-only history of decisions taken on a review item | `id`, `review_id` (FK), `decision`, `decided_by`, `decided_at`, `note`, `case_revision`, `content_hash` |
| `citation` | Instruments and cases cited (CELEX/ECLI) | `id`, `case_id` (FK), `target_identifier`, `citation_type` |
| `ingest_run` | One row per pipeline execution | `id`, `jurisdiction_code`, `connector`, `started_at`, `finished_at`, `status`, `fetched_count`, `matched_count`, `inserted_count`, `updated_count`, `skipped_duplicate_count`, `error_count`, `checkpoint_before`, `checkpoint_after` |
| `ingest_checkpoint` | Resumable position per connector | `connector` (PK), `jurisdiction_code`, `last_modified_seen`, `last_cursor`, `updated_at` |

**Deduplication key:** `UNIQUE (jurisdiction_code, source_id)`. On conflict, compare
`content_hash`; identical → touch `last_seen_at` only; different → update in place and
record the revision (`case.revision` is incremented). Never insert a second row for the same
source identifier.

**`keyword_match` matters:** it is how the content manager evaluates and tunes the keyword
lists. Do not treat it as optional.

**The review queue (core document §2.7).** A case that passes its list's `min_score` but
scores below `min_score + review_band` is stored, published and *additionally* queued. The
rules the schema enforces:

- `case.needs_review` and `case.filter_score` are the filter's own output and are rewritten
  by every evaluation. They are never changed by a decision: re-running a window must produce
  the same flags whether or not anyone has reviewed in the meantime (§2.8). The workflow
  lives in `case_review.status`.
- **A decision survives re-ingestion.** `case_review` is not among the child rows a run
  replaces. A re-run whose `content_hash` equals `flagged_content_hash` leaves the row
  untouched, timestamps included.
- **A genuine upstream revision re-opens the review.** A different `content_hash` returns
  `status` to `pending` and refreshes the `flagged_*` columns, while `decision`,
  `decided_by`, `decided_at` and `decided_revision` stay as they were — the previous verdict
  is visible, and visibly not about the current text.
- **A rejection withholds publication and deletes nothing.** `case.is_published` goes false;
  the case, its documents and its `keyword_match` rows remain. `suppressed_publication`
  records that the review is what withheld it, so a later confirmation restores exactly that
  and does not publish a case an editor unpublished for another reason.
- A case that leaves the band on a later evaluation retires an *undecided* item as
  `withdrawn`; a decision that has been taken stands regardless.

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
zero-case jurisdictions retained), `list_facets`, `latest_successful_runs`, and for the queue
`search_reviews` / `count_reviews` (taking a `ReviewSearchCriteria`), `get_review_by_id` and
`record_review_decision`. Clamping `page`, `page_size` and `limit` against `Settings` stays
the caller's job.

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
        needs_review: bool, threshold and review_ceiling, and a human-readable reason
        for the pipeline report."""
```

`FilterableDocument` is a structural protocol over `jurisdiction_code`, `title`, `abstract`,
`subject` and `full_text`, so no import couples a stage to the connector work stream. Stage 1
is the keyword matcher; a later stage appends to the `FilterChain` and touches no connector.

**Passed, and passed confidently, are two different answers.** `passed` decides whether the
document enters the database and is answered generously, because the PLT optimises for recall
(core document §2.7). `needs_review` qualifies it: the document scored inside its list's
`scoring.review_band`, the interval immediately above `min_score`, and is published like any
other while also entering the review queue. `FilterResult.passed_confidently` is the negation
pair, and `threshold`/`review_ceiling` state the band the verdict was measured against so a
stored verdict stays readable after the list is re-curated. A rejection never carries the
flag. The chain propagates a flag raised by **any** stage onto the result it returns, so
appending a stage cannot silently empty the queue.

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
| `GET` | `/api/cases/export` | Same filters as `/api/cases`, plus `format` (`csv` default, or `jsonl`). Not paginated: it streams every match |
| `GET` | `/api/stats/jurisdictions` | Map payload — **one query, all jurisdictions, EU included** |
| `GET` | `/api/filters` | Facet values for the All-cases filter UI |
| `GET` | `/api/health` | Liveness + last successful ingest per jurisdiction |
| `GET` | `/api/reviews` | **Authenticated.** The review queue. Query params: `status` (repeatable, `pending` default, `any` for every status), `jurisdiction` (repeatable), `list_version`, `decided_by`, `sort` (`flagged_asc` default, `flagged_desc`, `score_asc`, `score_desc`), `page`, `page_size` |
| `POST` | `/api/reviews/<id>/decision` | **Authenticated.** Record a confirmation or a rejection |

**The two review routes are not public.** They list cases a rejection has unpublished — which
`/api/cases` reports as absent — and can unpublish more, so they require a bearer token
(`PLT_REVIEW_API_TOKEN`, compared in constant time) and answer `503 review_queue_disabled`
when none is configured: an unset secret closes the queue, it never opens it. The token
travels in the `Authorization` header, so the state-changing route is not reachable by a
cross-site form post and needs no CSRF token of its own. The reviewer identity `decided_by`
is an opaque string: the content manager may be a person or an agent (core document §2.7),
and neither is assumed.

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

**`/api/reviews`** — the same paginated envelope as `/api/cases`, whose `items` are
`ReviewItem`s. `/api/reviews/<id>/decision` answers `200` with the single `ReviewItem` as it
stands after the decision.

**`ReviewItem`** — one queue entry, carrying everything a reviewer needs to decide **without
a second request**: the case, the numbers the flag was derived from, and the matched terms
that produced them. Nothing is recomputed; it is what the run recorded.

```json
{ "id": 12, "status": "pending",
  "score": 3.5, "min_score": 3.0, "band_ceiling": 6.0, "list_version": "1.1.0",
  "reason": "score 3.5 reaches min_score 3, inside the review band [3, 6) (NL list v1.1.0); matched: nl-drift",
  "flagged_at": "2026-08-05T06:00:00+00:00", "flagged_revision": 1,
  "flagged_content_hash": "9f2c…",
  "decision": null, "decided_by": null, "decided_at": null, "decided_revision": null,
  "decision_note": null, "decision_is_current": false,
  "case_revision": 1, "published": true,
  "case": CaseSummary,
  "keyword_matches": [ … as in CaseDetail … ],
  "decisions": [{ "decision": "confirmed", "decided_by": "…", "decided_at": "…",
                  "note": null, "case_revision": 1, "content_hash": "9f2c…" }] }
```

- `status` is the workflow: `pending` is the queue, `confirmed` / `rejected` mirror the
  standing decision, `withdrawn` marks an item that left the band before anyone decided.
- `decision` and its companions are the **standing** decision and survive a re-flag, so a
  reviewer sees what was last concluded. `decision_is_current` says whether it was taken on
  the revision now in front of them — the server states it rather than leaving a client to
  compare `decided_revision` against `case_revision` and get it wrong.
- `decisions` is the full history, oldest first, including verdicts a later revision
  superseded.
- `published` is the one place the editorial `is_published` switch is exposed, because a
  reviewer must be able to see that a rejection took effect. It stays off `CaseSummary`, and
  the public endpoints still report an unpublished case as a 404.

**`POST /api/reviews/<id>/decision`** takes
`{"decision": "confirmed" | "rejected", "decided_by": "…", "note": "…"}`. `decision` and
`decided_by` are required; `note` is optional. `decided_by` is bounded at 255 characters and
`note` at 4000, and neither may contain control characters.

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
per client) and a stricter limit on `/api/cases/export` and on `/api/reviews`; CORS
restricted to configured origins; no stack traces, SQL or file paths in responses — an
unexpected exception logs server-side and answers with a generic `internal_error` envelope.
The review routes add `401 unauthorized` for a missing or wrong bearer token and
`503 review_queue_disabled` when none is configured; a rejected token is logged without its
value.

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
  hover affordance must have a focus equivalent. Coverage is `docs/core-document.md` Annex 2
  and comes from the geometry, not from the response, so a jurisdiction the API does not
  mention is still drawn. The geometry is a **generated local asset**
  (`frontend/src/components/map/geometry.generated.ts`, written by
  `frontend/scripts/generate-map-geometry.mjs`): no mapping library, no tile server, no
  runtime third-party request.
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
