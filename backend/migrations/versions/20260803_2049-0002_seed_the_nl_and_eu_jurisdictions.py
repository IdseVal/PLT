"""Seed the NL and EU jurisdictions.

The two launch jurisdictions are reference data, not ingested data: the map, the filter UI
and every foreign key from ``case`` depend on these rows existing before the first pipeline
run. The EU is seeded as its own ``supranational`` jurisdiction rather than as an
aggregation of its member states (core document section 3.3).

``map_feature_id`` is the identifier the frontend map resolves a jurisdiction against.
States carry their ISO 3166-1 alpha-2 code, which is the feature id of the country shape;
the EU carries the sentinel ``EU``, which the map renders as the hoverable logo in the North
Sea instead of a shape (architecture section 6).

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-03 20:49:08.284868+00:00
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Lightweight table definition: the ORM model is deliberately not imported, so that this
# revision keeps applying unchanged after the model evolves.
jurisdiction = sa.table(
    "jurisdiction",
    sa.column("code", sa.String),
    sa.column("name", sa.String),
    sa.column("type", sa.String),
    sa.column("iso_alpha2", sa.String),
    sa.column("map_feature_id", sa.String),
    sa.column("default_language", sa.String),
    sa.column("is_active", sa.Boolean),
    sa.column("source_metadata", sa.JSON),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

SEEDED_CODES: tuple[str, ...] = ("EU", "NL")


def _rows() -> list[dict[str, Any]]:
    """Build the seed rows, stamped with a single timestamp for the whole revision."""
    now = datetime.now(UTC)
    return [
        {
            "code": "EU",
            "name": "European Union",
            "type": "supranational",
            "iso_alpha2": None,
            "map_feature_id": "EU",
            "default_language": "en",
            "is_active": True,
            "source_metadata": {
                "court": "Court of Justice of the European Union",
                "identifier_scheme": "CELEX",
                "portal_url": "https://eur-lex.europa.eu",
            },
            "created_at": now,
            "updated_at": now,
        },
        {
            "code": "NL",
            "name": "Netherlands",
            "type": "state",
            "iso_alpha2": "NL",
            "map_feature_id": "NL",
            "default_language": "nl",
            "is_active": True,
            "source_metadata": {
                "identifier_scheme": "ECLI",
                "portal_url": "https://www.rechtspraak.nl",
            },
            "created_at": now,
            "updated_at": now,
        },
    ]


def upgrade() -> None:
    """Insert the NL and EU jurisdiction rows that are not already there.

    Only the missing ones, because this revision has to be re-appliable. ``downgrade`` keeps
    a seeded jurisdiction that has cases attached (see below), so going forward again on a
    populated database meets a row that is already present, and an unconditional insert dies
    on the primary key.

    Reading the present codes first and inserting the difference keeps this to ordinary
    ``SELECT`` and ``INSERT``, which SQLite and PostgreSQL both accept unchanged. An
    ``INSERT ... ON CONFLICT DO NOTHING`` would be shorter but is a dialect-specific
    construct in SQLAlchemy, and this project's schema is portable by rule
    (``docs/architecture.md`` section 3).
    """
    bind = op.get_bind()
    present = set(
        bind.scalars(sa.select(jurisdiction.c.code).where(jurisdiction.c.code.in_(SEEDED_CODES)))
    )
    missing = [row for row in _rows() if row["code"] not in present]
    if missing:
        op.bulk_insert(jurisdiction, missing)


def downgrade() -> None:
    """Remove the seeded rows, unless cases are attached to them.

    A jurisdiction that already carries ingested cases is left in place: dropping it would
    cascade to every case of that jurisdiction, and unwinding reference data must never
    destroy what the pipeline collected.
    """
    case_table = sa.table("case", sa.column("jurisdiction_code", sa.String))
    attached = (
        sa.select(sa.literal(1))
        .select_from(case_table)
        .where(case_table.c.jurisdiction_code == jurisdiction.c.code)
        .exists()
    )
    op.execute(jurisdiction.delete().where(jurisdiction.c.code.in_(SEEDED_CODES), ~attached))
