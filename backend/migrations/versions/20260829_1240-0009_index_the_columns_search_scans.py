"""Index the columns search scans, on PostgreSQL.

Case search matches a substring: ``repositories.py`` wraps the term in ``%`` and runs
``ILIKE`` against ``case.title``, ``case.abstract``, ``case.source_id`` and, through an
``EXISTS``, ``case_document.full_text``. A leading wildcard means no B-tree index can serve
it, so every search reads every document.

That is invisible on a development SQLite file and expensive on a served database. Measured
against the corpus as it stood at this revision — 4,339 cases, 5,622 documents, 158 MB of
full text — on PostgreSQL 16 with no index:

===========================  ==========  =========
Query                        Before      After
===========================  ==========  =========
``glyfosaat`` (73 hits)      2.09 s      0.088 s
``omwonenden`` (287 hits)    2.06 s      0.272 s
no match at all              1.94 s      0.047 s
===========================  ==========  =========

The hit counts are identical before and after, because a trigram index accelerates the query
that is already written rather than replacing it.

**Why trigrams rather than full-text search.** A ``tsvector`` would be faster still, and would
be wrong here. Dutch compounds: a stemmer indexes whole words, so a search for
``gewasbeschermingsmiddel`` would not find ``gewasbeschermingsmiddelenwet`` — the statute whose
name contains it. The same holds for ``bestrijdingsmiddelenwet``, for the ``spuitzone``
compounds, and for a great deal of German when that jurisdiction is onboarded. Substring
matching is the semantics this corpus wants, and ``_ordering()``'s relevance ranking, which is
a ``CASE`` over the same patterns, keeps working untouched.

**Why this revision is dialect-guarded.** ``pg_trgm`` is a PostgreSQL extension. SQLite has no
equivalent and needs none: development databases are small, and the point of the guard is that
the migration set stays a single sequence both dialects can run, as revision 0001 established.
On SQLite this revision does nothing and says so.

The full-text index is the only large one — 17 MB against 158 MB of text, roughly a ninth.
Watch it as jurisdictions are added, but it is not the cost the corpus is.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-29 12:40:00.000000+00:00
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

#: One entry per column the search filter touches: index name, table, column.
_TRIGRAM_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_case_title_trgm", "case", "title"),
    ("ix_case_abstract_trgm", "case", "abstract"),
    ("ix_case_source_id_trgm", "case", "source_id"),
    ("ix_case_document_full_text_trgm", "case_document", "full_text"),
)


def upgrade() -> None:
    """Create the trigram indexes, on PostgreSQL only."""
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for name, table, column in _TRIGRAM_INDEXES:
        op.execute(f'CREATE INDEX {name} ON "{table}" USING gin ({column} gin_trgm_ops)')


def downgrade() -> None:
    """Drop the trigram indexes.

    The extension is left installed: it is shared by definition, and dropping something another
    database in the same cluster may be using is not this revision's decision to make.
    """
    if op.get_bind().dialect.name != "postgresql":
        return

    for name, _table, _column in reversed(_TRIGRAM_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
