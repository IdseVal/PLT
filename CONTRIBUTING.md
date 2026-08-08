# Contributing to the PLT

Everything you need to run the Pesticide Litigation Tracker locally, and the rules a change
has to satisfy before it can be merged.

Before writing code, read [`docs/architecture.md`](docs/architecture.md). It is the
integration contract between work streams: the repository layout (§1), the cross-cutting
rules (§2), the database schema (§3), the pipeline interfaces (§4), the HTTP API (§5) and
the frontend contract (§6). Anything crossing a module boundary is fixed there. If a
contract is wrong, change the document in the same pull request and say so in the
description.

---

## 1. Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Python | 3.11+ | The backend is fully type-annotated and checked with mypy. |
| Node.js | 20+ | Ships the npm version the lockfile was written with. |
| Git | any recent | Branch from `dev`, never from `main`. |

## 2. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cp .env.example .env               # then edit; the defaults run out of the box
alembic upgrade head               # creates ./plt.db and seeds the jurisdictions
flask --app plt.app run
```

The API listens on <http://127.0.0.1:5000>. Check it:

```bash
curl http://127.0.0.1:5000/api/health
# {"ingest":{},"service":"plt-api","status":"ok","version":"0.1.0"}
```

**If port 5000 is taken**, run the API somewhere else and point the frontend proxy at it —
both halves, never only the first:

```bash
flask --app plt.app run --port 5055                     # backend
# frontend/.env.local:  VITE_DEV_API_PROXY=http://127.0.0.1:5055
```

Windows and macOS both hand port 5000 out to something else — AirPlay Receiver on macOS, and
on either, a Flask server left running in another checkout. Werkzeug does not always refuse
to start on a port already in use, so the symptom is not an error: the page loads, the API
calls 404, and the 404s come from a process you are not looking at. Moving the port and
leaving `VITE_DEV_API_PROXY` alone produces exactly that.

Configuration is read from the environment (`PLT_*`) or `.env`, and every variable is
documented in [`backend/.env.example`](backend/.env.example). Nothing in the code may
hard-code a URL, path, credential, page size or rate limit — add a field to
`plt.config.Settings` instead, and document it in `.env.example`.

**Never commit a `.env`.** It is git-ignored; `.env.example` is the template, and it holds
placeholders only.

### Database and migrations

SQLite in development, PostgreSQL-compatible for deployment. Alembic reads the database URL
from settings, not from `alembic.ini`:

```bash
cd backend
alembic revision --autogenerate -m "add case table"
alembic upgrade head
```

#### When two branches add a migration at the same time

Two open pull requests that each add a revision both branch from the same head, so both
number their file `000N` and both point `down_revision` at `000N-1`. Each is a valid chain on
its own branch and each migrates cleanly there — and merged they are two heads, at which
point `alembic upgrade head` refuses to run at all and **no fresh database can be created**.
That is not a rare shape; it is what happens whenever two schema changes are open in the same
window, and it is what put two `0004` revisions on `dev` (issue #79).

CI's **Migrations** step fails this on the merge result and names the two files. Alembic's own
account of the same state is `Multiple heads are present for given argument 'head'; 0004,
0004`, from inside a very long traceback, and it names neither file. To see the check yourself,
merge `dev` in and run:

```bash
cd backend
pytest tests/unit/test_migration_graph.py tests/unit/test_migrations.py
alembic heads          # the same thing alembic's way: exactly one revision, or it collided
```

To resolve a collision, **renumber the later migration and leave the earlier one alone**:

1. Merge or rebase `dev` in, so both revisions are in your tree.
2. Rename your file `..._000N_....py` to `..._000N+1_....py`.
3. Inside it, set `revision = "000N+1"` and `down_revision = "000N"`.
4. `alembic upgrade head`, `alembic downgrade base`, `alembic upgrade head` — all three, on a
   throwaway database. A single head is not the same as a chain that runs both ways.

**Never renumber the migration that is already on `dev`.** Its identifier is written into the
`alembic_version` row of every database that has run it — a colleague's `plt.db`, staging,
production — and changing it strands them on a revision that no longer exists. The unmerged
one is the one nobody has applied yet, so it is the one that is free to move.

Two things this guard cannot do for you. It sees a collision only when CI runs, so a pull
request approved before a colliding migration merged is stale, and **re-running CI before
merging is what makes the check binding** (the repository setting for this is *Require
branches to be up to date before merging*). And "one head" is a statement about the graph,
not about the schema: two migrations that both add a `court.type` column will chain happily
and still fail on the second one, so read what else is open before you write the revision.

### CLI

```bash
python -m plt.cli --help           # or: plt --help
flask --app plt.app plt --help     # the same commands, through the Flask CLI
```

## 3. Frontend

```bash
cd frontend
npm ci
cp .env.example .env.local         # optional; the defaults work with the dev proxy
npm run dev
```

The dev server runs on <http://localhost:5173> and proxies `/api` to
`http://127.0.0.1:5000`, so run the backend alongside it. Both are only defaults:
`VITE_DEV_PORT` and `VITE_DEV_API_PROXY` in `.env.local` replace them, and the startup banner
reports the port actually in use. Only `VITE_`-prefixed variables reach the browser bundle —
never put a secret in one. These two are the exception in the other direction: they configure
the dev-server process rather than the bundle, so they mean nothing to a built site.

| Command | What it does |
| --- | --- |
| `npm run dev` | Dev server with hot reload |
| `npm run build` | Typecheck, then production build into `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm run lint` | ESLint, type-aware |
| `npm run test` | Vitest once (`npm run test:watch` to iterate) |
| `npm run test:a11y` | Accessibility and responsive checks in a real browser (see §4) |
| `node scripts/generate-map-geometry.mjs` | Regenerate the map's geometry asset (see below) |

The jurisdiction map ships **pre-projected**: `scripts/generate-map-geometry.mjs` turns Natural
Earth data into plain SVG path strings once, and
`src/components/map/geometry.generated.ts` is committed. Nothing in the browser projects
geometry or contacts a tile server, so the map works offline. Do not edit the generated file;
change the script and re-run it. `--check` fails when the committed file is out of date, and
the script needs the network only on a first run, after which the source data is cached.

Which jurisdictions the map draws is decided by `docs/core-document.md` Annex 2, which both the
script and `tests/mapGeometry.test.ts` read. The annex carries names only — no ISO codes and no
geometry — so a member state added to it needs two things written in the test as well as in the
script's `GEOMETRY_BY_JURISDICTION`, on purpose:

| In the test | What it stops |
| --- | --- |
| `JURISDICTION_CODES`: the alpha-2 code | A code paired with the wrong country, which puts one member state's case count and `/cases` link on another's shape. |
| `JURISDICTION_LOCATIONS`: roughly where the country is, in lon/lat | An outline taken from the wrong Natural Earth id, which draws one member state's coastline under another's name. |

Both are one line each, and both are held against ISO 3166-1 as Node's region data reports it,
so editing a pin until it agrees with a broken asset does not make the test pass. Write the
location from an atlas or from memory — a rough centre to a tenth of a degree is what is wanted,
and the tolerance is measured in hundreds of kilometres.

## 4. Tests and checks

CI runs exactly these on every pull request to `dev`, and a red check blocks the merge.

```bash
# backend/
ruff check .            # lint
ruff format --check .   # formatting
mypy .                  # strict typing, no untyped signatures
pytest                  # unit tests; add -m integration for the live-endpoint ones

# frontend/
npm run lint
npm run typecheck
npm run test
npm run build
npm run test:a11y       # needs `npm run build` first, and a browser (below)
```

CI additionally runs the **Migrations** step ahead of those, because a broken revision chain
means no database can be created and nothing else in the job is worth reading. It is the one
check that is not equivalent to running the command locally: it judges your branch *merged
into* `dev`, including any migration that landed there after your last push. See "When two
branches add a migration at the same time" above.

Tests are part of the deliverable, not an afterthought (`docs/architecture.md` §2.8):

- Unit tests never touch the network. Record a source payload once into
  `backend/tests/fixtures/` and mock the HTTP client against it.
- Tests that do hit a live endpoint are marked `@pytest.mark.integration` and are opt-in.
- **A recording is evidence about payloads, not about behaviour** (§2.9). The moment your
  fake has to decide what *order* to return rows in, whether a repeated request answers the
  same way, or what a retry does, it is inventing behaviour the recording never contained —
  and a fake that invents something better behaved than the real endpoint will pass a
  connector that is broken. That is not hypothetical: `FakeCellar` sorted stably, CELLAR does
  not, and a discovery walk that silently lost 14.9% of the EU corpus passed the whole suite.
  Pin the behaviour with an integration test instead. For anything that pages a window, the
  test already has a shape — walk it in pages and assert the union equals the count the
  source itself reports for the same window, asserting **both** that nothing is missing and
  that nothing came back twice. See `test_a_paged_window_yields_every_case_the_endpoint_counts`
  and `test_a_paged_window_yields_every_ecli_the_feed_counts`.
- Frontend tests render through `@testing-library/react` and assert on accessible roles and
  names, so the tests break when the page stops being accessible.

### The accessibility and responsive checks

`npm run test:a11y` serves the **production build** with a stubbed API and drives headless
Chrome over every route, in every data state that route can be in, at 320, 414, 768, 1024,
1280 and 1440 px. It checks four things:

| Check | Why a unit test cannot |
| --- | --- |
| axe-core, `wcag2a` / `wcag2aa` / `wcag21a` / `wcag21aa` / `best-practice` | Some rules need layout and a stacking context. |
| Colour contrast, measured on rendered pixels | `tests/theme.test.ts` bans ad-hoc colour *values*; only a browser knows what a token looks like once composited on the background it ends up over. |
| No horizontal overflow, `scrollWidth === clientWidth` | jsdom has no layout at all. |
| A visible focus indicator at every tab stop | `:focus-visible` depends on how focus arrived. This is the check that catches a control the site-wide focus ring silently does not reach. |

The browser is deliberately **not** installed by `npm ci` — most work in this repository does
not need a 150 MB Chrome. Install it once:

```bash
cd frontend
npx puppeteer browsers install chrome
```

`puppeteer` and `axe-core` are pinned to exact versions. A new axe minor adds rules and a new
Chrome changes layout; either would turn CI red on a pull request that touched neither, which
is how a check stops being read. **Bump them in their own pull request**, with the resulting
diff in the report.

**Contrast is enforced, not reported**, even though the palette is a placeholder until the
Wageningen Law styling package lands (README §7). `tailwind.config.js` records a contrast
ratio per token; a failure today is a regression against a budget the project has written
down. When the real palette arrives and does not hold AA, this job going red is the point of
having it. So that such a pull request can still be assembled in steps,
`A11Y_CONTRAST=report npm run test:a11y` prints contrast findings instead of failing on
them — for that one run, never as a setting in CI.

Useful while iterating:

```bash
npm run test:a11y -- --only=cases      # one route
npm run test:a11y -- --width=320       # one width
npm run test:a11y -- --concurrency=1   # serially, for readable output
```

**Fonts are the one thing the harness cannot pin down.** The site runs on a system font stack
until the styling package arrives, so glyph widths are whichever machine is measuring: the
3 px overflow this harness first found on `/cases` shows with Segoe UI and not with the
Liberation faces on `ubuntu-latest`. A run is reproducible on a platform, approximate across
platforms — the CI job is the authority, and a width that only just fits is not really
passing anywhere. Real font files will end this.

## 5. Running the ingestion pipeline

The weekly scan (`docs/core-document.md` §2.6, `docs/architecture.md` §7) is `plt ingest`.
The scheduled workflow, a server cron and your terminal all call that one command — the
scheduler is a trigger, never a second implementation, so anything you can reproduce here is
what the weekly job does.

### Locally

```bash
cd backend
python -m plt.cli jurisdictions                     # what --all would run, from the registry
python -m plt.cli ingest -j NL --dry-run            # every stage, no database changes
python -m plt.cli ingest -j NL                      # the real thing, incremental
python -m plt.cli ingest --all --fail-on-partial    # what the weekly job runs
```

**Run without `--since`.** The stored checkpoint supplies the window, which is what makes the
run incremental and safe to repeat; `--since` is for deliberately re-crawling a period. A
dry run writes a JSON Lines match report — every document judged, accepted *and* rejected —
to `PLT_PIPELINE_REPORT_DIR`, or to `--report PATH`.

Exit codes, because a scheduler acts on them:

| Code | Meaning |
| --- | --- |
| `0` | The run completed. |
| `1` | The run failed. The checkpoint was not advanced, so the window is retried next time. |
| `2` | Usage error (click's own): a bad option or an unknown jurisdiction. |
| `3` | Only with `--fail-on-partial`: the run completed but documents failed. |
| `130` | Interrupted (`Ctrl+C`). The document in flight finished and its batch was committed. |

`3` exists because a `partial` run stops advancing the checkpoint at the first failed
document. That is the correct behaviour — the window is retried rather than skipped — but for
an unattended job it means a jurisdiction can stay frozen for months while every run reports
success. Pass `--fail-on-partial` anywhere nobody is reading the output; leave it off
interactively, where `0` still means "the run completed".

### On a schedule, in GitHub Actions

[`.github/workflows/weekly-ingest.yml`](.github/workflows/weekly-ingest.yml) runs Mondays at
04:20 UTC, one job per jurisdiction, and the matrix is built from `plt jurisdictions` so
onboarding a jurisdiction never means editing the workflow. It needs one repository secret:

| Secret | Purpose |
| --- | --- |
| `PLT_DATABASE_URL` | SQLAlchemy URL of the database to write to. Never echoed by the workflow. |

Without it a **live** run refuses to start, and a **dry** run falls back to a throwaway
SQLite file so the workflow can still be exercised. To run it by hand: *Actions → Weekly
ingest → Run workflow*, which offers a jurisdiction (or `all`), a `dry_run` box that is
**ticked by default**, and an optional window. Untick `dry_run` only when you mean to write.

Each job appends its jurisdiction's counts to the run summary, and a dry run uploads its
match report as an artifact.

> GitHub disables scheduled workflows in a repository with no activity for 60 days, and
> queues `schedule` events on a busy fleet rather than firing them punctually. Neither is a
> problem for a weekly incremental scan — a late run covers the same window — but if the
> schedule matters more than that, run it from a server instead.

### On a schedule, from a server cron

The same command, with the environment supplied by the deployment rather than by `.env`:

```cron
# m  h  dom mon dow
  20 4  *   *   1  cd /srv/plt/backend && /srv/plt/.venv/bin/plt ingest --all --fail-on-partial >> /var/log/plt/ingest.log 2>&1
```

Two things to add in production:

- **A lock, so two runs cannot overlap.** The workflow gets this from its concurrency group;
  a crontab needs `flock`, and the pipeline holds no lock of its own:

  ```cron
  20 4 * * 1 /usr/bin/flock -n /var/lock/plt-ingest.lock -c 'cd /srv/plt/backend && /srv/plt/.venv/bin/plt ingest --all --fail-on-partial' >> /var/log/plt/ingest.log 2>&1
  ```

  `-n` skips the run rather than queueing it: the next scan picks the window up from the
  checkpoint, so a skipped run loses nothing.
- **Something that reads the exit code.** `cron` mails a non-zero exit to the crontab's
  owner; make sure that mailbox goes somewhere a person looks, or wrap the command in
  whatever alerting the deployment already has.

Set `PLT_LOG_FORMAT=json` for a log a collector can parse, keep `PLT_DATABASE_URL` in the
unit's environment file (mode `0600`, never in the repository), and leave the politeness
settings alone — `PLT_HTTP_REQUESTS_PER_SECOND` and the backoff are what keep the project
welcome at a public court endpoint.

### The corpus mirror, and the log each run leaves

`plt mirror` (`docs/architecture.md` §9) copies a jurisdiction's source payloads to disk
verbatim. It is not an ingestion: it writes no database row and reads no keyword list.

```bash
cd backend
python -m plt.cli mirror -j EU --limit 5 --store ./scratch-store   # a rehearsal
python -m plt.cli mirror -j EU                                     # the real thing
```

The store is `PLT_CORPUS_STORE_DIR`; `--store PATH` overrides it for one run, which is how you
rehearse without touching the real corpus. Run without `--since` — the store's own checkpoint
supplies the window, so a run resumes rather than starting over, and a case already on disk
costs no request.

**Every run writes one log**, whether it finished, failed or was interrupted:

```
<PLT_CORPUS_STORE_DIR>/EU/logs/2026-W32_20260809T031205Z.log
```

The name is the ISO week the run belongs to and the exact UTC instant it started, so a weekly
job never overwrites last week's record, two runs in one week each keep their own, and sorting
by name sorts by date. Open one and it says, in plain text and without needing the code:

- when it ran, how long it took, and whether it finished, failed or was interrupted;
- the window it covered, as the checkpoint before and after — which is what says how much of
  the source it actually looked at, and whether the position moved at all;
- how many cases were discovered, newly fetched, **already on disk and skipped**, and failed.
  That middle distinction is the one to read first: *skipped because already present* against
  *newly fetched* is what separates a genuinely quiet week from a run that did nothing;
- what failed and why, summarised, pointing at `_failures.jsonl` for the full history;
- how many requests it made, how many were retries, and every `Retry-After` it honoured;
- how many cases the store holds now.

Every log is kept by default. `PLT_CORPUS_LOG_RETENTION_RUNS=52` keeps the newest 52 per
jurisdiction and deletes what is older; leaving it unset keeps everything, and nothing this
project did not write is ever deleted. `manifest.json`, `_checkpoint.json` and
`_failures.jsonl` beside `logs/` are unchanged — the capture's scope, the resume position, and
every failure the store has ever seen.

## 5a. The mailing list and the digest

The subscriber alert (`docs/architecture.md` §8) is `plt digest`, and the same
trigger-not-a-second-implementation rule applies: the scheduled workflow, a server cron and
your terminal all call the one command.

**A checkout cannot email a real person.** `PLT_MAIL_BACKEND` defaults to `console`, which
renders each message to the log and opens no socket. Set it to `file` when you want to read
what a subscriber would receive:

```bash
cd backend
PLT_MAIL_BACKEND=file PLT_MAIL_OUTBOX_DIR=./outbox flask --app plt.app run
# subscribe on http://localhost:5173, then open the .eml the outbox now holds and
# follow the confirmation link in it.
python -m plt.cli digest --dry-run          # render every message, send none
python -m plt.cli digest                    # the real send, through the configured backend
```

Only `PLT_MAIL_BACKEND=smtp` reaches a mail server, and it needs `PLT_SMTP_HOST` before the
settings will validate. Production refuses the `console` backend outright: a confirmation
written to the log there is a subscriber left waiting for an email that was never sent.

Two things to know before touching this code:

- **The window identifies a send.** `plt digest --until` pins it; repeating a run with the
  same window reaches only the recipients the previous attempt did not, which is how you
  resume an interrupted digest. Re-running without `--until` opens a *new* window, and a case
  still inside it is announced again.
- **Nothing may report who is on the list.** The subscribe endpoints answer identically
  whatever they found, no repository helper lists the table, and no subscriber address is
  logged at any level. If a change would make a known address distinguishable from an unknown
  one — in a status code, a body, an error, or a log line — it is a contract change and needs
  `docs/architecture.md` §3 and §5 updated with it.
- **An unsubscribe destroys the address and is not undone by a stranger.** Cancelling replaces
  `subscriber.email` with `HMAC-SHA256(pepper, address)` under `PLT_SUBSCRIPTION_ADDRESS_PEPPER`
  (core document §2.12), so submitting that address again is recognised and sends *nothing*.
  Two consequences for anyone working here: the pepper never goes in a column, a fixture or a
  migration, and normalisation lives in exactly one function
  (`plt.notifications.pseudonyms.normalise_address`) — a second `.lower()` anywhere would break
  recognition without failing a test that was not looking for it. Call it pseudonymisation, not
  anonymisation; the digest is reversible to whoever holds the pepper, which is what recognising
  a returning address means.
- **Retention is unset on purpose.** `PLT_SUBSCRIBER_RETENTION_DAYS` and
  `PLT_SUBSCRIBER_UNCONFIRMED_EXPIRY_DAYS` have no defaults, because the periods are the Law
  group's to decide (issue #75). `plt purge-subscribers` applies whichever is configured and
  reports "not configured" for whichever is not. Do not fill one in to make the output tidier.

The scheduled send is [`.github/workflows/weekly-digest.yml`](.github/workflows/weekly-digest.yml),
triggered by the *completion of the scheduled ingest* so it announces the cases that scan
just landed. Beyond `PLT_DATABASE_URL` it needs the mail secrets to send anything live:

| Secret | Purpose |
| --- | --- |
| `PLT_SMTP_HOST`, `PLT_SMTP_USERNAME`, `PLT_SMTP_PASSWORD` | The mail server. Without the host the job runs the console backend, and a *live* run refuses to start. |
| `PLT_SUBSCRIPTION_TOKEN_SECRET` | Key the confirm and unsubscribe tokens are derived from. Rotating it invalidates every link already sent. |

`PLT_SITE_BASE_URL`, `PLT_MAIL_FROM` and `PLT_SMTP_PORT` are repository *variables*, not
secrets. `PLT_ADMIN_EMAIL` belongs wherever `plt ingest` runs: it is the address the review
queue's notice goes to, and unset means no notice is sent.

## 6. Conventions

**Python.** Full type annotations, complete docstrings, `ruff` and `mypy` clean. Long loops
must be interruptible (`KeyboardInterrupt` finishes the item in flight, writes the
checkpoint and exits), stream rather than accumulate, and release every resource in a
`finally`.

**TypeScript.** No `any`. All server data flows through `src/api/client.ts`; no component
calls `fetch`. Tailwind theme tokens (`bg-plt-surface`, `text-plt-ink`, `text-plt-muted`,
`border-plt-border`, `text-plt-accent-strong`, `bg-plt-accent-deep`, `text-plt-inverse`)
rather than hex values in components. Every control labelled, focus visible, contrast
checked.

> Tailwind emits **no CSS and no error** for a class that does not exist, so a token name
> that has drifted produces silently unstyled markup. `frontend/tailwind.config.js` is the
> only list that counts — read it rather than copying class names from another component.
> `frontend/tests/theme.test.ts` catches hex values, not stale token names.

The palette and font stack are **placeholders** awaiting the Wageningen Law styling package
(README §7). Keep every visual decision expressible through the tokens so the swap stays a
one-file change, and do not derive or approximate WUR corporate branding in the meantime.

**Logging.** Structured and levelled through `plt.utils.logging`. Identifiers such as an
ECLI or CELEX number are fine; case text, personal data, tokens and credentials are not.

**Sources are public research endpoints.** Respect the configured request rate, back off on
429 and 5xx, and keep the descriptive `User-Agent`. Never hammer them.

## 7. Branches, commits and pull requests

- Branch from `dev`: `feature/<issue-number>-<short-description>` or
  `fix/<issue-number>-<short-description>`.
- Commit atomically, one logical change each, with a message explaining what changed and
  why. No tool signatures or co-author trailers.
- Open the pull request against `dev` and include `Closes #<issue-number>`.
- `main` is release-only: never push to it, and never merge into it outside a release PR.

A change is done when the code lints and typechecks, the tests pass, every acceptance
criterion in the issue is addressed, and the pull request says which checks were run.

## 8. Data files

Keyword lists in [`data/keywords/`](data/keywords/) are **curated data owned by the content
manager**, not code. Validate against `schema.json`, bump `list_version`, and record the
reasoning in `notes`. A jurisdiction cannot be onboarded to the pipeline before its list
exists — see [`data/keywords/README.md`](data/keywords/README.md).

`case_sensitive` and `match` are declared on a term and applied to **every alias it carries**,
which the schema cannot express and cannot check. The loader therefore rejects a
`case_sensitive` literal that is not acronym-shaped and a `substring` literal shorter than six
characters, naming the term and the literal; three defects have shipped through that gap, so
the failure is deliberate and is not to be worked around by widening the term.
