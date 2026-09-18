"""Record which legislation list a run applied.

A corpus has to be able to say which list produced it (core document section 2.8). Until
now the only record was ``keyword_match.list_version``, repeated on every match and absent
from a run that matched nothing, and it is the version the file claimed rather than the
file itself. Two columns on ``ingest_run`` close that gap: the list version the run loaded,
and the SHA-256 of the list file, so two runs under one claimed version can still be told
apart when the file differed between them.

Both are nullable. Rows written before this revision have nothing to put in them, and a
chain assembled without a legislation stage - a test, a future classifier-only run - has
no list to name.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-17 15:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the list version and digest to ``ingest_run``."""
    with op.batch_alter_table("ingest_run", schema=None) as batch_op:
        batch_op.add_column(sa.Column("list_version", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("list_digest", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Drop the two columns again."""
    with op.batch_alter_table("ingest_run", schema=None) as batch_op:
        batch_op.drop_column("list_digest")
        batch_op.drop_column("list_version")
