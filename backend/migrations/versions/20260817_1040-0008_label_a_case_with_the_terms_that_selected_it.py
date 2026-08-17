"""Label a case with the terms that selected it.

Selection stopped being arithmetic. A document is now in scope because a curated term matched
it, so the term and its category are facts about the case worth publishing rather than working
notes behind a score — the case list is filtered by them and the case page lists them.

Three changes, all of them consequences of that one:

* ``keyword_match.category`` is added and indexed. The index is what makes "every case
  labelled ``active_substance``" a lookup rather than a scan of every match ever recorded.
* ``keyword_match.weight_applied`` is dropped. There is no weight to apply. The column also
  held the evidence that a ``requires`` gate had stayed shut, recorded as ``0.0``; a gated
  term now selects and labels nothing and so is not written at all, which is why the table
  after this revision is exactly the list of labels and nothing else.
* ``case.filter_score`` becomes ``case.matched_term_count``, and changes type with its name.
  Leaving a column called "score" holding a count of terms is the kind of artefact that gets
  quoted years later as though it were the thing it is named after.
* ``case_review`` loses ``score``, ``min_score`` and ``band_ceiling``. All three described a
  document's distance from the selection threshold, and there is no longer a threshold to be
  at a distance from. They are dropped rather than left nullable because a column nothing can
  ever populate reads, to the next person, as a column something forgot to populate. The two
  score orderings of the review queue go with them.

The queue itself stays. What changes is that nothing raises a flag automatically any more:
"borderline" meant "just above the threshold", so a content manager now flags an item, and the
machinery that publishes, decides, withdraws and re-opens on an upstream revision is untouched.

**This does not migrate the data, and cannot.** A score is not recoverable from a count and a
count is not recoverable from a score, and the labels this revision makes room for were never
recorded — the old table stored the inflection found in the text, not the curated term it
belongs to. Every case in the tracker is therefore re-derived from the corpus mirror after
this revision, which costs one local run over payloads already on disk and no requests to
either source.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-17 10:40:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the category label, drop the weight, and rename the score to what it now counts."""
    with op.batch_alter_table("keyword_match", schema=None) as batch_op:
        batch_op.add_column(sa.Column("category", sa.String(length=64), nullable=True))
        batch_op.create_index("ix_keyword_match_category", ["category"], unique=False)
        batch_op.drop_column("weight_applied")

    with op.batch_alter_table("case", schema=None) as batch_op:
        batch_op.drop_column("filter_score")
        batch_op.add_column(sa.Column("matched_term_count", sa.Integer(), nullable=True))

    with op.batch_alter_table("case_review", schema=None) as batch_op:
        batch_op.drop_column("score")
        batch_op.drop_column("min_score")
        batch_op.drop_column("band_ceiling")


def downgrade() -> None:
    """Restore the weighted columns, empty.

    The values are not restored because they were never derivable from what replaced them.
    A downgrade therefore returns the shape and leaves the re-derivation to a run, exactly as
    the upgrade does.
    """
    with op.batch_alter_table("case_review", schema=None) as batch_op:
        batch_op.add_column(sa.Column("score", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("min_score", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("band_ceiling", sa.Float(), nullable=True))

    with op.batch_alter_table("case", schema=None) as batch_op:
        batch_op.drop_column("matched_term_count")
        batch_op.add_column(sa.Column("filter_score", sa.Float(), nullable=True))

    with op.batch_alter_table("keyword_match", schema=None) as batch_op:
        batch_op.add_column(sa.Column("weight_applied", sa.Float(), nullable=True))
        batch_op.drop_index("ix_keyword_match_category")
        batch_op.drop_column("category")
