# Alembic migrations

Revisions live in `versions/`. Run every command from `backend/`, with the target database
set through `PLT_DATABASE_URL` (or `.env`) — `alembic.ini` deliberately holds no URL.

```bash
alembic revision --autogenerate -m "add case table"   # generate from plt.db.models
alembic upgrade head                                  # apply
alembic downgrade -1                                  # revert the last revision
```

Autogenerate compares the database against `plt.db.base.metadata`. A model that is not
imported by `plt/db/models.py` is invisible to it. Always read a generated revision before
committing it: SQLite reflects less than PostgreSQL does, so type changes and constraint
drops need checking by hand.

`alembic check` reports any drift between the models and the applied revisions; it runs as
part of the test suite (`tests/unit/test_migrations.py`), so a model change without a
migration fails CI.

`env.py` renders `plt.db.base.UtcDateTime` as `sa.DateTime(timezone=True)`, so a committed
revision never imports application code — the DDL is identical either way.

Revision *files* are stamped with a UTC timestamp (`timezone = UTC` in `alembic.ini`), which
needs a timezone database. Linux and macOS have one; on Windows install `tzdata`
(`pip install -e ".[dev]"` covers it). Applying migrations needs nothing extra.
