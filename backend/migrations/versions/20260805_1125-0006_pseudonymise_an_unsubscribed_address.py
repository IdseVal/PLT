"""Pseudonymise an unsubscribed address.

Core document section 2.12: when somebody unsubscribes, the address is replaced by a keyed
one-way digest. The row survives so the project keeps its records and its statistics; the
address does not.

``email`` becomes nullable and ``email_digest`` holds
``HMAC-SHA256(pepper, normalised_address)`` for a row whose subscription has ended. **The
pepper is not here and never will be.** It comes from ``PLT_SUBSCRIPTION_ADDRESS_PEPPER`` at
run time, so a dump of this database yields no addresses — which is the entire difference
between a pseudonym and a lookup value, because the space of email addresses is enumerable
and a bare hash of one is a word list away from the address itself.

Two check constraints hold the shape:

* ``address_or_digest_not_both`` — a row holds the address or its digest, never both, so the
  substitution cannot be half-done and quietly keep the personal data it was meant to drop.
* ``address_present_unless_unsubscribed`` — only an unsubscribed row may lack an address, so
  a row the digest send is supposed to reach can never be one it cannot address.

Both permit a row with *neither*, which is a real state twice over: the backfill below, and
an unsubscribed row whose digest has been dropped once ``PLT_SUBSCRIBER_RETENTION_DAYS`` has
passed, leaving dates and a counter.

``digest_count`` counts digests **sent** to the row. It is the one statistic that could not
be recovered from the timestamps once the address is gone, which is why it becomes a column
rather than staying a derivation. It records nothing about delivery, opening or clicking —
the tracker has never held any of that.

**Backfill: an address already unsubscribed is dropped, not digested.** Computing a digest
here would mean reading the pepper inside a migration, and the whole point of the pepper is
that it never appears in the database or in anything that produces it offline. So rows that
unsubscribed before this revision lose their address and gain no digest: strictly more
protective than the policy requires, and it costs only the ability to recognise those
particular addresses if they come back. The list has never run outside a developer's
checkout, so this affects no real subscriber.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-05 11:25:23.981127+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    with op.batch_alter_table("subscriber", schema=None) as batch_op:
        batch_op.add_column(sa.Column("email_digest", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("digest_count", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.alter_column("email", existing_type=sa.VARCHAR(length=254), nullable=True)
        batch_op.create_unique_constraint(
            batch_op.f("uq_subscriber_email_digest"), ["email_digest"]
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_subscriber_address_or_digest_not_both"),
            "email IS NULL OR email_digest IS NULL",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_subscriber_address_present_unless_unsubscribed"),
            "email IS NOT NULL OR status = 'unsubscribed'",
        )

    # The backfill. See the module docstring: no pepper reaches a migration, so an address
    # that had already been withdrawn is dropped rather than replaced by a digest.
    op.execute(
        sa.text("UPDATE subscriber SET email = NULL WHERE status = 'unsubscribed'")  # noqa: S608
    )


def downgrade() -> None:
    """Revert this revision.

    ``email`` goes back to ``NOT NULL``, which the pseudonymised rows cannot satisfy — the
    address they would need is precisely what this revision destroyed, and no downgrade can
    invent it. They are deleted, which loses their dates and their counter. That is the
    honest cost of unwinding a one-way transformation, and it is stated here rather than
    discovered as a constraint violation half way through.
    """
    op.execute(sa.text("DELETE FROM subscriber WHERE email IS NULL"))

    with op.batch_alter_table("subscriber", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_subscriber_address_present_unless_unsubscribed"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_subscriber_address_or_digest_not_both"), type_="check"
        )
        batch_op.drop_constraint(batch_op.f("uq_subscriber_email_digest"), type_="unique")
        batch_op.alter_column("email", existing_type=sa.VARCHAR(length=254), nullable=False)
        batch_op.drop_column("digest_count")
        batch_op.drop_column("email_digest")
