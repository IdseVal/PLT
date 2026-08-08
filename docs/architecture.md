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
      subscriptions.py      /api/subscriptions* — the mailing list (§8), public
      schemas.py            Request/response (de)serialisation + validation
      errors.py             Uniform error envelope
    notifications/
      mailer.py             Console / file / SMTP backends behind one interface
      messages.py           Every message the tracker sends, as plain text
      tokens.py             Purpose-bound HMAC tokens for confirm and unsubscribe
      pseudonyms.py         Address normalisation + the keyed digest that replaces it
      subscriptions.py      The subscribe / confirm / unsubscribe lifecycle
      retention.py          Storage limitation: purge_subscribers, unset = not enforced
      digest.py             The weekly send: batched, resumable, interruptible
      reviews.py            The administrator's notice that the queue has items
    pipeline/
      runner.py             Orchestrator: run_jurisdiction(code, since=None)
      base.py               SourceConnector ABC + RawDocument/NormalisedCase dataclasses
      checkpoint.py         Per-connector checkpoint read/write
      mirror.py             The corpus mirror: source payloads on disk, verbatim (§9)
      dedup.py              Source-identifier and content-hash deduplication
      filters/
        base.py             Filter ABC (the chain is pluggable)
        keywords.py         Stage 1: keyword matcher over data/keywords/*.json
      connectors/
        rechtspraak.py      NL
        eurlex.py           EU
    cli.py                  `flask plt ...` / `python -m plt.cli` entry points
    utils/logging.py        Structured logging setup
    utils/shutdown.py       StopRequest: the graceful-shutdown flag every long job uses
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
.github/workflows/          ci.yml, weekly-ingest.yml, weekly-digest.yml
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
   Never accumulate a full corpus in memory or in one transaction. Where a source cannot be
   walked a page at a time without losing rows, a connector may hold one discovery *window*
   — but only against a configured ceiling on how large a window may be, so that what it
   holds is bounded by a setting rather than by how much case law the window happens to
   contain, and it must say in the log when a window exceeds that ceiling anyway.
   `EurLexConnector` is the one that does this, and `eurlex_max_results` is the ceiling.
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
| `court` | Courts/instances, seeded from source vocabularies | `id`, `jurisdiction_code` (FK), `source_identifier` (unique per jurisdiction), `name`, `level`, `domain`, `source_type` |
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
| `subscriber` | One address on the mailing list (§8) | `id`, `email` (**unique**, null once unsubscribed), `email_digest` (**unique**, the keyed digest that replaces it), `status` (`pending`\|`confirmed`\|`unsubscribed`), `token_seed` (**unique**), `created_at`, `updated_at`, `notice_sent_at`, `confirmed_at`, `unsubscribed_at`, `last_digest_at`, `digest_count` |

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

**`subscriber` holds personal data, and its column list is the rule.** An email address is
personal data under the GDPR and Wageningen University is an EU institution, so the table is
a deliberate minimum and adding to it is a decision, not a convenience:

- **What is stored:** the address, the state, the timestamps that make consent auditable, and
  the selector half of the token. **What is not:** a name, an IP address, a user agent, a
  referrer, or any record of a message being opened or a link being followed. A digest
  carries no tracking pixel and no rewritten links, so there is nothing of that kind to hold.
- **`status` is the double opt-in.** A row is `pending` until its confirmation link is used,
  and **only a `confirmed` row is ever sent a digest**. `unsubscribed` is kept rather than
  deleted so a withdrawal is a fact on the record; `unsubscribed_at` is what the retention
  purge works from.
- **Unsubscribing replaces the address with a keyed digest** (core document §2.12). `email`
  becomes null and `email_digest` holds `HMAC-SHA256(pepper, normalised_address)` under
  `PLT_SUBSCRIPTION_ADDRESS_PEPPER`, which is held outside the database, so a dump of this
  table on its own yields no addresses — which a *bare* hash of an email would, since the
  address space is enumerable and a candidate list matches it in one pass. Two check
  constraints hold the shape: a row carries the address or the digest and never both, and an
  unsubscribed row holds no address while a row with no address is unsubscribed.
  - **This is pseudonymisation, not anonymisation, and must be described as such** in code,
    documentation and API. The digest is deterministic because a returning address has to be
    recognisable, and determinism is what makes it reversible to anyone holding the pepper.
    A subject access request still reaches these rows and storage limitation still applies.
    Suppression and full anonymisation are mutually exclusive; this project chose suppression.
  - **The pepper is long-lived.** Rotating it makes every already-pseudonymised row
    unrecognisable, and there is no re-keying path. Production refuses to start without an
    explicit one rather than falling back to `PLT_SECRET_KEY`, whose rotation is routine.
  - **Normalisation happens once**, in `plt.notifications.pseudonyms.normalise_address`, which
    is also what `plt.api.schemas` stores: strip, then lower-case the whole address. Two rules
    would break recognition silently, since an unrecognised returning address is
    indistinguishable from a new one. No provider-specific folding (dots, `+tags`): an
    over-broad digest suppresses somebody who never unsubscribed.
- **`digest_count` is why the statistics survive the address.** Subscription date,
  confirmation date, unsubscribe date, tenure and digests sent are all on the row, so nothing
  in the reporting path needs the address. It counts what was handed to the mail backend, not
  what was delivered, opened or clicked — none of which is recorded anywhere.
- **Retention is configuration with no default** (§8). `PLT_SUBSCRIBER_RETENTION_DAYS`
  drops the digest from an unsubscribed row, leaving dates and the counter;
  `PLT_SUBSCRIBER_UNCONFIRMED_EXPIRY_DAYS` deletes a row that never confirmed. Unset means
  **not enforced**, never a guessed number: both periods are the Law group's to decide.
- **`token_seed` is a selector, not a token.** A link carries `<seed>.<verifier>`, where the
  verifier is `HMAC-SHA256(key, "plt.subscription.v1:<purpose>:<seed>")` under
  `PLT_SUBSCRIPTION_TOKEN_SECRET` (defaulting to `PLT_SECRET_KEY`), base64url and never
  stored. A database dump therefore yields no working confirmation or unsubscribe link, the
  purpose is inside the HMAC so one link cannot be replayed as the other, and comparison is
  `hmac.compare_digest`. A re-subscription rotates the seed, which retires the previous
  subscription's links.
- **Expiry is on the row, not in the token.** `notice_sent_at` is both the confirmation
  deadline (`PLT_SUBSCRIPTION_CONFIRM_TTL_HOURS`) and the per-address throttle
  (`PLT_SUBSCRIPTION_NOTICE_INTERVAL_SECONDS`), so the deadline can be seen, changed and
  audited, and a link can be retired by rotating a seed rather than by waiting for a claim to
  lapse.
- **`last_digest_at` is a position, not telemetry.** It holds the end of the last digest
  window the address was sent, which is what makes an interrupted send resumable. It records
  nothing about what the reader did.
- **The table may never be listed.** It is read by exactly one address or one verified seed
  at a time (see the repository helpers below), and the endpoints above it answer identically
  whether or not a row exists.

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
  Union, which the map renders as the hoverable North Sea logo instead of a shape. It is
  **`NOT NULL`**, because the map indexes its payload on it and a row without one is a
  jurisdiction that can never be joined to a shape — drawn permanently as "no cases yet"
  however many cases it holds, which is a silent coverage hole rather than a visible fault.
  Adding a jurisdiction therefore means stating its feature id, and the client may rely on
  the field being present.
- The subject-matter classification a connector reads — the *rechtsgebied* for the
  Netherlands — has no column here and is stored as `case.source_metadata["subject"]`. It is
  a **scored** field of the filter chain nonetheless; see §4.2.
- `court.level` and `court.domain` are this project's normalisation across jurisdictions and
  are what the API filters on. `court.source_type` is the source's own word for the same
  thing, verbatim, stored **beside** them and never instead of them: the normalisation is
  deliberately lossy — Rechtspraak's `Koninkrijksinstantie`, the Caribbean courts of the
  Kingdom, flattens onto the same `other` as every residual instance — and rule 2.6 applies
  to a vocabulary exactly as it does to a judgment. Whatever else a court vocabulary states
  and no column holds goes to `court.source_metadata`. Both are written on every upsert, so
  `plt seed-vocabularies` is a re-statement of the vocabulary and not an accumulation.

**Repository helpers** (`plt/db/repositories.py`) are the only SQL the API layer calls:
`search_cases` / `count_cases` / `stream_cases` (all taking a `CaseSearchCriteria`),
`latest_cases`, `get_case_by_source_id`, `get_case_by_id`, `get_case_fingerprint` (the
pipeline's dedup pre-check), `jurisdiction_stats` (the one-query map payload, EU included and
zero-case jurisdictions retained), `list_facets`, `latest_successful_runs`, and for the queue
`search_reviews` / `count_reviews` (taking a `ReviewSearchCriteria`), `get_review_by_id` and
`record_review_decision`. Clamping `page`, `page_size` and `limit` against `Settings` stays
the caller's job.

The notification layer adds `cases_first_seen` / `count_cases_first_seen` (the digest window,
over `first_seen_at` and published cases only), `reviews_flagged_since` /
`count_reviews_flagged_since` (the administrator's notice), and four subscriber helpers:
`get_subscriber_by_email`, `get_subscriber_by_email_digest`, `get_subscriber_by_token_seed`
and `confirmed_subscribers_after`. **There is deliberately no `search_subscribers` and no
`count_subscribers`.** The first three answer a caller that already knows the address, or
holds a verified token — a digest is not something a caller can supply, only something
derived from an address under a key it does not have — and the fourth is keyset pagination for
the digest send, filtered to `confirmed`. Nothing in the API layer may list this table, and
adding a helper that could is a contract change, not a refactor.

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
plt mirror --jurisdiction EU [--since ...] [--until ...] [--store DIR] [--limit N]
plt digest [--since ...] [--until ...] [--dry-run]
plt purge-subscribers
plt jurisdictions [--json]
plt seed-vocabularies --jurisdiction NL
```

Timestamps are parsed as UTC. Exit codes: `0` completed, `1` failed, `130` interrupted, and
`3` for a run that completed with failures under `--fail-on-partial`.

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
| `POST` | `/api/subscriptions` | Public. Take an address and email it a confirmation link. Body `{"email": "..."}` |
| `POST` | `/api/subscriptions/confirm` | Public. Complete the double opt-in. Body `{"token": "..."}` |
| `POST` | `/api/subscriptions/unsubscribe` | Public. End a subscription immediately. Body `{"token": "..."}` |
| `POST` | `/api/subscriptions/unsubscribe-link` | Public. Email an address its own unsubscribe link. Body `{"email": "..."}` |

**The two review routes are not public.** They list cases a rejection has unpublished — which
`/api/cases` reports as absent — and can unpublish more, so they require a bearer token
(`PLT_REVIEW_API_TOKEN`, compared in constant time) and answer `503 review_queue_disabled`
when none is configured: an unset secret closes the queue, it never opens it. The token
travels in the `Authorization` header, so the state-changing route is not reachable by a
cross-site form post and needs no CSRF token of its own. The reviewer identity `decided_by`
is an opaque string: the content manager may be a person or an agent (core document §2.7),
and neither is assumed.

**The four subscription routes are public, and every one of them is `POST`.** They are the
only unauthenticated, state-changing, mail-sending routes in the API, and three rules bind
them:

- **An address never appears in a URL.** It travels in a JSON body, so it does not reach the
  browser history, an access log or a `Referer` header. There is no `GET` on any of them, and
  no route takes an address as a path or query parameter.
- **Subscribe and unsubscribe-link never vary their answer.** Both return `202` with one
  fixed body whatever the server found — unknown address, pending, confirmed, previously
  unsubscribed, or throttled out of a message entirely. A response that varied would be an
  address-checking oracle for anyone with a word list. `confirm` and `unsubscribe` *do*
  report success or failure, because the caller holds a token only the address itself could
  have received, and what they report is about the token: a forged token, an expired one and
  one whose subscription has since gone are all `400 invalid_token`, and the token is never
  echoed back into the message.
- **Unsubscribe is `POST` on purpose.** The link in an email points at the site's
  `/unsubscribe` page, which posts the token, so a mailbox provider's link scanner following
  the URL cannot cancel a subscription; `List-Unsubscribe-Post` (RFC 8058) still lets a mail
  client do it in one click.

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

**The subscription routes** answer one shape, `{"status": ..., "message": ...}`, and nothing
else. There is no subscriber object on the wire in either direction, because there is no
endpoint that reads one out.

```json
{ "status": "accepted", "message": "If that address needs an email from us, one is on its way. …" }
```

| Route | Status | `status` | What it means |
| --- | --- | --- | --- |
| `POST /api/subscriptions` | `202` | `accepted` | The request was taken. **Nothing more**: this is the same body whether the address was unknown, pending, confirmed, throttled, or previously unsubscribed and therefore suppressed |
| `POST /api/subscriptions/unsubscribe-link` | `202` | `accepted` | The same body, and the same silence about what was found |
| `POST /api/subscriptions/confirm` | `200` | `confirmed` | The address is on the list. Idempotent: a second use of the link answers identically |
| `POST /api/subscriptions/unsubscribe` | `200` | `unsubscribed` | The address is off the list, **and the address itself is gone** — replaced by its keyed digest (§3, core document §2.12). Idempotent, and a verified token whose row has gone also reports success |

**A pseudonymised row is not a new oracle.** Suppressing a withdrawn address adds a branch
that sends no mail, and the response it produces is byte-identical to every other class. The
silence is not a signal either: the per-address notice interval is keyed on the address rather
than on the caller, so any address at all can be one that a given request sends nothing for,
however fresh the client. What suppression does change is that a *first* submission from an
unthrottled client can now send no message, where before every class produced one — a timing
difference, not a response difference, observable only against a live SMTP server. That
channel already exists and is already accepted for `unsubscribe-link`, which sends nothing for
an address that is not on the list, and the same per-client limit bounds it.

`message` is written for a reader and is what the page shows, so it may be reworded; `status`
is the contract. A malformed address is `400 validation_error`, a token that does not verify
(for any reason) is `400 invalid_token`, and a mail backend that could not accept the message
is `503 mail_unavailable` — never with the backend's own error text, which names the server.

**`/api/stats/jurisdictions`** — a bare array, one entry per jurisdiction, ordered by code,
**including jurisdictions whose `case_count` is `0`** so the map renders intended coverage
in its muted state rather than dropping the shape:

```json
[{ "code": "EU", "name": "European Union", "type": "supranational",
   "map_feature_id": "EU", "is_active": true,
   "case_count": 12, "latest_decision_date": "2024-05-01" }]
```

Only `latest_decision_date` may be null here. `code`, `name`, `map_feature_id` and
`case_count` are always present and non-null — `map_feature_id` because §3 makes the column
`NOT NULL`, so the statement is enforced rather than merely intended. A client may
nonetheless resolve a jurisdiction whose `map_feature_id` is absent against its `code`,
which §3 makes the same value for a state and for the Union: **a jurisdiction that named and
counted itself is never discarded for failing this contract**, because dropping it loses a
published case silently, and a whole payload failing it would otherwise be indistinguishable
from an unreadable response.

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

The subscription routes carry the strictest limits in the API, because they are
unauthenticated and send email: `PLT_RATE_LIMIT_SUBSCRIBE` (default `5 per hour`) on the two
that mail an address a caller supplied, and `PLT_RATE_LIMIT_SUBSCRIPTION_TOKEN` (default
`30 per hour`) on confirm and unsubscribe. **The per-client limit is not the whole defence**:
`PLT_SUBSCRIPTION_NOTICE_INTERVAL_SECONDS` caps how often any one *address* can be written
to, whatever the source of the requests, which is what makes the form useless as a way of
bombarding a third party.

**No subscriber address is logged**, at any level, and neither is a token: the routes log an
outcome and an internal id. The `console` mail backend is the one deliberate exception — it
renders whole messages to the log so a developer can follow a confirmation link locally, and
it is refused in production for exactly that reason (§8). The subscription routes need no
CSRF token: they carry no session, cookie or credential a browser would attach on a caller's
behalf, and each requires a JSON body, which an HTML form cannot send and a cross-origin
`fetch` can only send after a preflight the CORS policy refuses.

---

## 6. Frontend contract

- **Routes:** `/` (home), `/cases` (all cases), `/cases/:jurisdiction/:sourceId` (detail),
  `/about`, `/methodology`, `/faq`, `/contact`, plus the two mailing-list routes
  `/subscribe/confirm` and `/unsubscribe`, which are reached from an emailed link or from the
  front-page form and are **not** in the site menu.
- **Header** on every route: branding + menu *About Wageningen Law*, *Methodology*, *FAQ*,
  *Contact*.
- **Home:** title "Pesticide Litigation Tracker (PLT)"; search bar directly beneath it; map
  below the search bar; right-hand sidebar with the 20 latest cases and a button to
  `/cases`. The email-alert signup is the only addition to that composition and sits **at the
  end of the right-hand column, below the sidebar's button**, so none of the four fixed
  elements moves.
- **`/unsubscribe`** works two ways: with the `token` from an email it cancels on load, with
  no button and no login, posting the token rather than following a link; without one it
  offers to email the link to an address, which is the only way to reach the flow from the
  site without either a login or an open invitation to cancel somebody else's subscription.
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
- **Server-supplied text is rendered through `src/utils/caseText.ts`, without exception.**
  `cleanInlineText` for a single-line value, `cleanBlockText` / `toParagraphs` for a body of
  text. React escapes markup; it does not remove control characters or bidirectional
  overrides, which are invisible and can make a string read differently from what it
  contains. The rule covers a jurisdiction name as much as a judgment title: a name is seeded
  by migration today, but a jurisdiction taken from a source vocabulary would arrive exactly
  as a court name does. Repo-authored strings — the fallback names in the generated geometry,
  for instance — are not server data and need no cleaning.
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

A second weekly job sends the subscriber digest (`.github/workflows/weekly-digest.yml`,
`plt digest`). It is triggered by the *completion of the scheduled ingest* rather than by a
clock of its own, so it announces the cases that scan just landed; a dispatched ingest, which
defaults to a dry run, does not trigger it. Runs cannot overlap, and the workflow pins
`--until` to the top of the hour so that repeating a failed send covers the same window and
therefore reaches only the recipients it missed.

`plt purge-subscribers` (§8) belongs on the same weekly schedule. It is safe to run before any
retention period has been decided: with neither configured it does nothing and says so.

---

## 8. Notifications

Two audiences, deliberately kept apart, and the constraints on each are contracts rather than
implementation choices.

**Readers** subscribe to the weekly digest from the front page. `subscriber` (§3) holds the
list; the endpoints are in §5; the sending is `plt.notifications`. Five rules:

1. **Double opt-in.** A submitted address is `pending` and receives exactly one message, its
   own confirmation request. It is sent no digest until the link is used.
2. **Unsubscribe needs no account.** A purpose-bound HMAC token in the link is the whole
   authorisation, it appears in the body *and* in `List-Unsubscribe` of every message the
   list sends, and it is honoured immediately.
3. **Unsubscribe is durable, and the address does not survive it.** The status change and the
   substitution of the address by its keyed digest happen in one transaction (§3, core
   document §2.12). Because the returning address stays recognisable, **a submission of an
   address that has unsubscribed sends nothing at all** — it is answered exactly as every
   other submission is. This is the one path by which a person who had withdrawn consent
   could previously receive mail because a *third party* typed their address into the form,
   and closing it is what makes "leave me alone" mean more than "until somebody types this
   again". The request cannot show whether the person themselves is back or a stranger is
   acting, and the mechanism that would settle it — mailing the address — is the harm; a
   returning subscriber's route back is the contact address, or the expiry of the suppression
   with `PLT_SUBSCRIBER_RETENTION_DAYS`.
4. **The minimum is stored, and no behaviour is recorded.** Plain-text messages only, so no
   tracking pixel; no link is rewritten through a redirector; one recipient per message, so a
   digest cannot disclose the list.
5. **Nothing may enumerate the list**, in the API (§5) or in the repository layer (§3).
6. **The abuse surface is bounded twice**: per client by the rate limit, per address by the
   notice interval (§5.2).

**Retention (`plt purge-subscribers`).** Storage limitation does not stop at pseudonymisation,
so a second job beside the digest applies whatever horizons a deployment has configured: it
drops the digest from an unsubscribed row after `PLT_SUBSCRIBER_RETENTION_DAYS`, leaving dates
and `digest_count`, and deletes an address that never confirmed after
`PLT_SUBSCRIBER_UNCONFIRMED_EXPIRY_DAYS` — that row records no consent, so nothing about it is
kept. **Both are unset by default and unset means not enforced**, because the periods are the
Law group's to decide and a default would be a policy nobody chose. The job says
"not configured" rather than reporting a count of zero, since the two look identical in a log
and mean opposite things.

**The administrator** is told when the review queue gains items, at `PLT_ADMIN_EMAIL`. One
message per scan, not one per case; the flags themselves stay off every public endpoint, so
this notification is what makes core document §2.7's review possible at all. Unset means no
notification, and the queue is unaffected either way. When the quarantine record of core
document §2.11 exists it belongs in the same message, for the same reason.

**Sending** goes through one interface with three backends, chosen by `PLT_MAIL_BACKEND`:
`console` (the default; renders to the log, opens no socket), `file` (writes `.eml` files),
and `smtp` (the standard library's `smtplib`, TLS verified). **A development checkout cannot
mail a real address**, production refuses the `console` backend, and there is no third-party
email service: a handful of plain-text messages a week from a university mail server is what
SMTP is for, and a vendor would add a data processor to a system holding personal data for no
capability in return.

---

## 9. The corpus mirror

`plt mirror` copies a jurisdiction's source payloads to disk, unfiltered and unclassified,
and is **not** an ingestion: it writes no database row and reads no keyword list. It exists
because core document §2.8 requires selection to be repeatable, and a live endpoint cannot
give that — two keyword lists scored against CELLAR a week apart differ by the repository as
well as by the list. Scored against a mirror they differ only by the list.

**Layout.** One directory per jurisdiction under `PLT_CORPUS_STORE_DIR`, one folder per case
inside it. The Dutch store already had this shape, so it is the shape:

```
<corpus_store_dir>/
  EU/
    62017CJ0616/
      metadata.json        index + provenance; written LAST, so it marks the case complete
      raw_content.xml      RawDocument.payload, verbatim
      fulltext.fr.xhtml    one per further language version, verbatim
    manifest.json          capture window, connector configuration, totals
    _checkpoint.json       where the next run resumes
    _failures.jsonl        the cases that did not come down, and why
    logs/
      2026-W32_20260809T031205Z.log   one readable record per run
```

**Rules.**

1. **Jurisdiction-agnostic.** The mirror drives a `SourceConnector` through the registry, so
   a jurisdiction is mirrored by the connector it was onboarded with and nothing here
   changes (§4).
2. **Verbatim** (rule 2.6). `RawDocument.payload` becomes `raw_content.<format>`; every
   further payload the normalised case carries becomes `fulltext.<language>.<format>`. A
   payload that *is* the source record is stored once, not twice.
3. **`metadata.json` is an index, not a second copy.** Identifiers, dates, court, language,
   the file list, and provenance — when the case was fetched and from what URL. The citation
   graph, the parties and the text of the decision are in the payloads beside it.
4. **Its checkpoint is a file, not the `ingest_checkpoint` row.** A mirror pass that advanced
   the ingestion position would make the pipeline skip cases it never ingested.
5. **A failed case holds the checkpoint back**, exactly as in the runner (§7). Re-enumerating
   costs discovery queries and no fetches, because everything already on disk is skipped.
6. **The store is configuration.** `PLT_CORPUS_STORE_DIR`, documented in `.env.example`. The
   built-in default is `./corpus` inside the checkout, git-ignored; a corpus outgrows a
   checkout, so a real deployment names the volume it lives on.
7. **Every run leaves one log a person can read**, in `logs/`, named for the ISO week and the
   instant it started — so a weekly job never overwrites last week's and two runs in one week
   never collide. It states the outcome, the window as the checkpoint before and after, the
   counts (discovered, newly fetched, **already held and skipped**, failed), a summary of the
   failures pointing at `_failures.jsonl`, the requests and every `Retry-After` honoured, and
   the store total afterwards. It is written on the failed and interrupted paths too: this
   command runs weekly with nobody watching, and a record that only appears on success cannot
   catch the failure it exists for (core document §2.7). A log that cannot be written is
   warned about and never fails the run. `PLT_CORPUS_LOG_RETENTION_RUNS` caps how many are
   kept; unset — the default — keeps every one, and only files the project wrote are ever
   deleted.
