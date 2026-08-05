"""Require a ``map_feature_id`` on every jurisdiction.

``jurisdiction.map_feature_id`` is not descriptive metadata: it is the identifier the
frontend map resolves a jurisdiction against — the ISO 3166-1 alpha-2 code for a state, the
sentinel ``EU`` for the Union (``docs/architecture.md`` section 3). The map indexes the
``/api/stats/jurisdictions`` payload on it, so a row without one is a jurisdiction that can
never be joined to a shape: it is drawn, permanently, as "no cases yet" however many cases it
holds. That is a silent coverage hole rather than a visible fault, which is why the column
becomes the database's rule instead of a convention the seed happens to keep.

**The backfill is derivable, so no row is lost.** ``COALESCE(map_feature_id, iso_alpha2,
code)`` reproduces exactly the convention section 3 states: a state's alpha-2 code is both its
``iso_alpha2`` and its jurisdiction code, and a supranational order has no ``iso_alpha2`` and
carries its code as the sentinel. ``code`` is the primary key, so the expression cannot
itself yield null.

``downgrade`` restores the nullability and leaves the values alone. A backfilled id is
indistinguishable from a seeded one after the fact, and it is the correct value either way.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-05 12:40:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Lightweight table definition: the ORM model is deliberately not imported, so that this
#: revision keeps applying unchanged after the model evolves.
jurisdiction = sa.table(
    "jurisdiction",
    sa.column("code", sa.String),
    sa.column("iso_alpha2", sa.String),
    sa.column("map_feature_id", sa.String),
)


def upgrade() -> None:
    """Backfill the missing identifiers, then make the column non-nullable."""
    op.execute(
        jurisdiction.update()
        .where(jurisdiction.c.map_feature_id.is_(None))
        .values(map_feature_id=sa.func.coalesce(jurisdiction.c.iso_alpha2, jurisdiction.c.code))
    )
    with op.batch_alter_table("jurisdiction", schema=None) as batch_op:
        batch_op.alter_column(
            "map_feature_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )


def downgrade() -> None:
    """Revert this revision, keeping the backfilled identifiers."""
    with op.batch_alter_table("jurisdiction", schema=None) as batch_op:
        batch_op.alter_column(
            "map_feature_id",
            existing_type=sa.String(length=64),
            nullable=True,
        )
