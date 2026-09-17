# Reconstituting the 0.1.0 corpus

This branch freezes the selection method the tracker used until September 2026: a
word search over the curated per-jurisdiction keyword lists in `data/keywords/`
(NL list 2.5.0, EU list 2.1.0, schema 2.0.0). The corpus that method produced is
not stored anywhere. It is regenerated from the mirror store and this checkout,
which together determine it completely (core document 2.8).

The mirror store is the only permanent store. Nothing below writes to it.

## Recipe

From a Windows workstation holding the store at `D:/CaseLawStore`:

```powershell
git worktree add ..\plt-0.1.0 0.1.0
cd ..\plt-0.1.0\backend
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -c constraints.txt -e .

$env:PLT_DATABASE_URL     = "sqlite+pysqlite:///D:/scratch/plt-0.1.0.db"
$env:PLT_CORPUS_STORE_DIR = "D:/CaseLawStore"
$env:PLT_MAIL_BACKEND     = "file"

alembic upgrade head
plt ingest -j NL -j EU --from-store --since 1900-01-01 --no-notify --batch-size 200
```

`alembic upgrade head` creates the schema and seeds the two jurisdictions; the
runner refuses a jurisdiction that has not been seeded.

`--since 1900-01-01` is not optional. Without it the window starts at the stored
checkpoint, and on a database that has ever been ingested into that rebuilds only
the cases modified after the checkpoint. On a fresh file the flag changes nothing,
which is the point: the command means the same thing on any database.

An interrupted run is simply run again. Deduplication skips what is stored without
opening the payload.

## What the result is

The store this was last run against held 945,823 Dutch cases (decisions 1994 to
2026-08-08) and 104,143 EU cases (1954 to 2026-08-06). A store that has been topped
up since will produce a superset, selected by the same rule; the `keyword_match`
rows record the list version that selected each case.

The expected corpus, as published on 2026-08-29: 3,027 Dutch and 1,312 EU cases.
