"""Add the mailing list.

One table, ``subscriber``, holding the addresses the weekly digest goes to. Core document
section 2.4 lists an alerting mailing list among the wanted features; this is the storage
half of it.

The column list is a deliberate minimum, because an address is personal data under the GDPR
and Wageningen University is an EU institution: the address, the state it is in, the
timestamps that make consent auditable, and the selector half of its token. No name, no IP
address, no user agent, no open or click record. A digest carries no tracking pixel, so
there is nothing of that kind for a column to hold.

``status`` carries the double opt-in: a row is ``pending`` until its confirmation link is
used, and only a ``confirmed`` row is ever sent a digest. ``unsubscribed`` is kept rather
than deleted, so a withdrawal is a fact on the record and a later re-subscription is a state
change on one row.

``token_seed`` is a random selector, not a token. The link carries ``<seed>.<verifier>``,
where the verifier is an HMAC-SHA256 of the seed and the purpose under a server-side key, so
this table alone yields no working confirmation or unsubscribe link.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-05 08:25:12.033951+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "subscriber",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "confirmed",
                "unsubscribed",
                name="subscriber_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("token_seed", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notice_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_digest_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriber")),
        sa.UniqueConstraint("email", name=op.f("uq_subscriber_email")),
        sa.UniqueConstraint("token_seed", name=op.f("uq_subscriber_token_seed")),
    )
    with op.batch_alter_table("subscriber", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_subscriber_status"), ["status"], unique=False)


def downgrade() -> None:
    """Revert this revision."""
    with op.batch_alter_table("subscriber", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_subscriber_status"))

    op.drop_table("subscriber")
