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
flask --app plt.app run
```

The API listens on <http://127.0.0.1:5000>. Check it:

```bash
curl http://127.0.0.1:5000/api/health
# {"ingest":{},"service":"plt-api","status":"ok","version":"0.1.0"}
```

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
`http://127.0.0.1:5000`, so run the backend alongside it. Only `VITE_`-prefixed variables
reach the browser bundle — never put a secret in one.

| Command | What it does |
| --- | --- |
| `npm run dev` | Dev server with hot reload |
| `npm run build` | Typecheck, then production build into `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm run lint` | ESLint, type-aware |
| `npm run test` | Vitest once (`npm run test:watch` to iterate) |

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

## 5. Conventions

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

## 6. Branches, commits and pull requests

- Branch from `dev`: `feature/<issue-number>-<short-description>` or
  `fix/<issue-number>-<short-description>`.
- Commit atomically, one logical change each, with a message explaining what changed and
  why. No tool signatures or co-author trailers.
- Open the pull request against `dev` and include `Closes #<issue-number>`.
- `main` is release-only: never push to it, and never merge into it outside a release PR.

A change is done when the code lints and typechecks, the tests pass, every acceptance
criterion in the issue is addressed, and the pull request says which checks were run.

## 7. Data files

Keyword lists in [`data/keywords/`](data/keywords/) are **curated data owned by the content
manager**, not code. Validate against `schema.json`, bump `list_version`, and record the
reasoning in `notes`. A jurisdiction cannot be onboarded to the pipeline before its list
exists — see [`data/keywords/README.md`](data/keywords/README.md).
