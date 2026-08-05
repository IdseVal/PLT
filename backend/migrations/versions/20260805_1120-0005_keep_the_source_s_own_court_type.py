"""Keep the source's own court type.

``court.level`` and ``court.domain`` are this project's normalisation of the type a source
states for a court, and the normalisation is lossy on purpose: Rechtspraak's
``Koninkrijksinstantie`` — the courts of Aruba, Curaçao, Sint Maarten and the BES islands,
which are Kingdom territory but outside the territorial scope of EU pesticide law — flattens
onto the same level ``other`` as the residual ``AndereGerechtelijkeInstantie``. Until now the
raw type was read at ingestion and then dropped, so nothing in the database could tell the
Caribbean courts from any other unclassified instance (``docs/jurisdictions/nl.md`` §1.1).

``court.source_type`` holds it verbatim, beside the normalised pair rather than instead of
it. Cross-cutting rule 6 of ``docs/architecture.md`` is the reason this is a migration at all:
the source states the type exactly once, when its vocabulary is read, so a column added later
would have been recoverable only by asking the source again.

**Existing rows are left null and no case has to be re-ingested.** Courts are seeded from a
controlled vocabulary rather than from case payloads, so the backfill is
``plt seed-vocabularies --jurisdiction NL --jurisdiction EU``: one request per source, and it
upserts on ``(jurisdiction_code, source_identifier)``, so it updates the rows already there.
Backfilling here instead would mean writing court names into a migration, which is exactly
what seeding from the vocabulary exists to avoid.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-05 11:20:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    with op.batch_alter_table("court", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_type", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Revert this revision."""
    with op.batch_alter_table("court", schema=None) as batch_op:
        batch_op.drop_column("source_type")
