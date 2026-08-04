# PLT backend

Flask API, SQLAlchemy 2.0 models, Alembic migrations and the ingestion pipeline for the
Pesticide Litigation Tracker. Layout and conventions are fixed by
[`docs/architecture.md`](../docs/architecture.md) §1–2.

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
flask --app plt.app run                        # http://127.0.0.1:5000/api/health
```

Checks, all of which CI runs on every pull request to `dev`:

```bash
ruff check . && ruff format --check .
mypy plt
pytest                 # unit tests; integration tests are opt-in with -m integration
```

## Dependencies

`pyproject.toml` declares permissive version ranges; [`constraints.txt`](constraints.txt)
pins the exact versions CI installs, the way `frontend/package-lock.json` does for the
frontend. To reproduce CI's environment locally, add it to the install:

```bash
pip install -c constraints.txt -e ".[dev]"
```

Upgrading a dependency means regenerating that file in its own pull request, so the bump is
reviewed rather than arriving unannounced with the next upstream release. Its header has the
commands.

Full setup, including the frontend, is in [`CONTRIBUTING.md`](../CONTRIBUTING.md).
