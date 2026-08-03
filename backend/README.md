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

Full setup, including the frontend, is in [`CONTRIBUTING.md`](../CONTRIBUTING.md).
