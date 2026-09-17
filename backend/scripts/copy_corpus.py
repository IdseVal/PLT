r"""Copy the regenerable tables of one PLT database into another.

A methodology change is rebuilt on the workstation, from the mirror, into a local database
(``docs/deployment.md`` section 3.4), and what reaches the server is that database's
output. This script is the shipping step for a rebuild made in SQLite: it copies the
regenerable tables - and only those - row for row, ids included, into a database that has
been migrated to the same revision, so the target can be a fresh PostgreSQL database on the
server reached over an SSH tunnel, or a second SQLite file.

The tables that hold operational state - ``subscriber``, ``case_review``,
``case_review_decision``, ``ingest_checkpoint`` - are never touched (section 3.5): a
subscriber's unsubscribe digest is unrecoverable, and the checkpoint is what the next weekly
run resumes from. ``ingest_run`` is copied, because the run rows are the record of which
list produced the corpus.

Usage::

    python scripts/copy_corpus.py \
        --source sqlite+pysqlite:///D:/scratch/plt-legislation.db \
        --target postgresql+psycopg://plt:...@127.0.0.1:5433/plt_incoming

The target must be empty in every table copied, or ``--replace`` must be given, in which
case those tables are emptied first, in one transaction with the copy.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from typing import Final, cast

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from plt.db.base import Base
from plt.db.models import (
    Case,
    CaseDocument,
    CaseTopic,
    Citation,
    Court,
    IngestRun,
    Jurisdiction,
    KeywordMatch,
    Party,
    Topic,
)

log = logging.getLogger("copy_corpus")

#: The regenerable tables, in an order that satisfies every foreign key on insert.
COPIED: Final[tuple[type[Base], ...]] = (
    Jurisdiction,
    Court,
    Topic,
    Case,
    CaseDocument,
    Party,
    CaseTopic,
    KeywordMatch,
    Citation,
    IngestRun,
)

#: Tables a freshly migrated target already holds rows in - the launch jurisdictions are
#: seeded by migration - and which are therefore replaced rather than required empty.
PRESEEDED: Final[frozenset[str]] = frozenset({"jurisdiction"})

#: Rows fetched and inserted per round trip. Full texts are large; a thousand is a few
#: megabytes.
BATCH: Final[int] = 1000


def _engine(url: str) -> Engine:
    """Build an engine for one side of the copy."""
    return sa.create_engine(url, future=True)


def _count(connection: sa.Connection, table: sa.Table) -> int:
    """Return how many rows a table holds."""
    return int(connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one())


def copy(source: Engine, target: Engine, *, replace: bool) -> None:
    """Copy every regenerable table from source to target, ids preserved.

    Args:
        source: The database the corpus was built in.
        target: The database to ship it to, migrated to the same revision.
        replace: Empty the copied tables in the target first.

    Raises:
        SystemExit: If the target already holds rows and ``replace`` is not set.
    """
    tables = [cast(sa.Table, model.__table__) for model in COPIED]
    with source.connect() as reading, target.begin() as writing:
        if not replace:
            occupied = [
                table.name
                for table in tables
                if table.name not in PRESEEDED and _count(writing, table)
            ]
            if occupied:
                log.error("target already holds rows in %s; pass --replace", ", ".join(occupied))
                raise SystemExit(2)
        for table in reversed(tables):
            if replace or table.name in PRESEEDED:
                writing.execute(sa.delete(table))
        for table in tables:
            total = _count(reading, table)
            copied = 0
            result = reading.execution_options(stream_results=True).execute(sa.select(table))
            while True:
                rows = result.fetchmany(BATCH)
                if not rows:
                    break
                writing.execute(sa.insert(table), [dict(row._mapping) for row in rows])
                copied += len(rows)
            log.info("%s: %d of %d rows copied", table.name, copied, total)
        if target.dialect.name == "postgresql":
            _reset_sequences(writing, tables)


def _reset_sequences(connection: sa.Connection, tables: Sequence[sa.Table]) -> None:
    """Move every copied table's id sequence past the ids that were copied in.

    Rows arrive with their ids, which PostgreSQL accepts without advancing the sequence
    behind the column; the next insert would then reuse an id and fail.

    Args:
        connection: Open connection to the target, inside the copy's transaction.
        tables: The tables that were copied.
    """
    for table in tables:
        for column in table.primary_key.columns:
            if column.type.python_type is not int:
                continue
            highest = connection.execute(sa.select(sa.func.max(column))).scalar_one()
            connection.execute(
                sa.text("SELECT setval(pg_get_serial_sequence(:table, :column), :next, false)"),
                {"table": table.name, "column": column.name, "next": int(highest or 0) + 1},
            )


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments, defaulting to the process's.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", required=True, help="SQLAlchemy URL of the built corpus")
    parser.add_argument("--target", required=True, help="SQLAlchemy URL of the database to fill")
    parser.add_argument("--replace", action="store_true", help="empty the copied tables first")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    copy(_engine(args.source), _engine(args.target), replace=args.replace)
    return 0


if __name__ == "__main__":
    sys.exit(main())
