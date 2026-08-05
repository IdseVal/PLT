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
```

Tests are part of the deliverable, not an afterthought (`docs/architecture.md` §2.8):

- Unit tests never touch the network. Record a source payload once into
  `backend/tests/fixtures/` and mock the HTTP client against it.
- Tests that do hit a live endpoint are marked `@pytest.mark.integration` and are opt-in.
- Frontend tests render through `@testing-library/react` and assert on accessible roles and
  names, so the tests break when the page stops being accessible.

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
