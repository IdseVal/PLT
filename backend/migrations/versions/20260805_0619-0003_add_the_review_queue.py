"""Add the review queue.

The selection policy of ``docs/CORE_DOCUMENT.md`` section 2.7 in the schema: a case that
passes the filter but scores inside its list's review band is ingested and published as
normal *and* queued for a content manager to confirm or reject.

Three additions:

* ``case.needs_review`` and ``case.filter_score`` — the flag and the score it was derived
  from, on the case itself. Existing rows are backfilled to ``false``: they were selected
  before the band existed and no run has judged them against it, so claiming they were
  reviewed, or that they need review, would both be untrue. The next run that touches a case
  sets the column from the verdict it computes.
* ``case_review`` — one row per flagged case: the score, threshold and list version that
  produced the flag, the standing decision, who took it and when.
* ``case_review_decision`` — the append-only history, so a decision superseded by a later
  upstream revision is still on the record.

Nothing here deletes or hides anything on its own. A rejection is applied by setting
``case.is_published`` to false; the case, its documents and its ``keyword_match`` evidence
stay exactly where they are.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-05 06:19:23.793152+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "case_review",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "confirmed",
                "rejected",
                "withdrawn",
                name="review_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("min_score", sa.Float(), nullable=True),
        sa.Column("band_ceiling", sa.Float(), nullable=True),
        sa.Column("list_version", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("flagged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("flagged_revision", sa.Integer(), nullable=True),
        sa.Column("flagged_content_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "decision",
            sa.Enum(
                "confirmed",
                "rejected",
                name="review_decision",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_revision", sa.Integer(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("suppressed_publication", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["case.id"],
            name=op.f("fk_case_review_case_id_case"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_review")),
        sa.UniqueConstraint("case_id", name=op.f("uq_case_review_case_id")),
    )
    with op.batch_alter_table("case_review", schema=None) as batch_op:
        batch_op.create_index(
            "ix_case_review_status_flagged_at", ["status", "flagged_at"], unique=False
        )

    op.create_table(
        "case_review_decision",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("review_id", sa.Integer(), nullable=False),
        sa.Column(
            "decision",
            sa.Enum(
                "confirmed",
                "rejected",
                name="review_decision_outcome",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("decided_by", sa.String(length=255), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("case_revision", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["case_review.id"],
            name=op.f("fk_case_review_decision_review_id_case_review"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_review_decision")),
    )
    with op.batch_alter_table("case_review_decision", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_case_review_decision_review_id"), ["review_id"], unique=False
        )
        batch_op.create_index(
            "ix_case_review_decision_review_id_decided_at",
            ["review_id", "decided_at"],
            unique=False,
        )

    with op.batch_alter_table("case", schema=None) as batch_op:
        batch_op.add_column(sa.Column("filter_score", sa.Float(), nullable=True))
        # The server default backfills the rows already in the table; it is dropped again
        # below so the column matches the model, whose default is applied in Python.
        batch_op.add_column(
            sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_index(batch_op.f("ix_case_needs_review"), ["needs_review"], unique=False)
    with op.batch_alter_table("case", schema=None) as batch_op:
        batch_op.alter_column("needs_review", server_default=None)


def downgrade() -> None:
    """Revert this revision."""
    with op.batch_alter_table("case", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_case_needs_review"))
        batch_op.drop_column("needs_review")
        batch_op.drop_column("filter_score")

    with op.batch_alter_table("case_review_decision", schema=None) as batch_op:
        batch_op.drop_index("ix_case_review_decision_review_id_decided_at")
        batch_op.drop_index(batch_op.f("ix_case_review_decision_review_id"))

    op.drop_table("case_review_decision")
    with op.batch_alter_table("case_review", schema=None) as batch_op:
        batch_op.drop_index("ix_case_review_status_flagged_at")

    op.drop_table("case_review")
