"""Create the case-law schema.

Every table of the contract in ``docs/architecture.md`` section 3: jurisdiction, court,
case, case_document, party, topic/case_topic, keyword_match, citation, ingest_run and
ingest_checkpoint.

Two properties of this revision are load-bearing:

* ``uq_case_jurisdiction_code_source_id`` is the deduplication key that makes the weekly
  pipeline safe to re-run. It is enforced here, by the database, not by application code.
* Timestamp columns are ``TIMESTAMP WITH TIME ZONE``; enumerated columns are ``VARCHAR``
  plus a ``CHECK`` constraint rather than a native PostgreSQL ``ENUM``, and JSON columns use
  the portable ``JSON`` type, so the same revision applies to SQLite and PostgreSQL.

Revision ID: 0001
Revises:
Create Date: 2026-08-03 20:48:17.227564+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "jurisdiction",
        sa.Column("code", sa.String(length=8), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "state",
                "supranational",
                name="jurisdiction_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("iso_alpha2", sa.String(length=2), nullable=True),
        sa.Column("map_feature_id", sa.String(length=64), nullable=True),
        sa.Column("default_language", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_jurisdiction")),
    )
    op.create_table(
        "topic",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["topic.id"], name=op.f("fk_topic_parent_id_topic"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topic")),
        sa.UniqueConstraint("slug", name=op.f("uq_topic_slug")),
    )
    with op.batch_alter_table("topic", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_topic_parent_id"), ["parent_id"], unique=False)

    op.create_table(
        "court",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("jurisdiction_code", sa.String(length=8), nullable=False),
        sa.Column("source_identifier", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("level", sa.String(length=64), nullable=True),
        sa.Column("domain", sa.String(length=64), nullable=True),
        sa.Column("abbreviation", sa.String(length=64), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["jurisdiction_code"],
            ["jurisdiction.code"],
            name=op.f("fk_court_jurisdiction_code_jurisdiction"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_court")),
        sa.UniqueConstraint(
            "jurisdiction_code",
            "source_identifier",
            name=op.f("uq_court_jurisdiction_code_source_identifier"),
        ),
    )
    with op.batch_alter_table("court", schema=None) as batch_op:
        batch_op.create_index("ix_court_jurisdiction_code", ["jurisdiction_code"], unique=False)

    op.create_table(
        "ingest_checkpoint",
        sa.Column("connector", sa.String(length=64), nullable=False),
        sa.Column("jurisdiction_code", sa.String(length=8), nullable=False),
        sa.Column("last_modified_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_cursor", sa.String(length=255), nullable=True),
        sa.Column("last_source_id", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["jurisdiction_code"],
            ["jurisdiction.code"],
            name=op.f("fk_ingest_checkpoint_jurisdiction_code_jurisdiction"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("connector", name=op.f("pk_ingest_checkpoint")),
    )
    op.create_table(
        "ingest_run",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("jurisdiction_code", sa.String(length=8), nullable=False),
        sa.Column("connector", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "success",
                "partial",
                "failed",
                "interrupted",
                name="ingest_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("fetched_count", sa.Integer(), nullable=False),
        sa.Column("matched_count", sa.Integer(), nullable=False),
        sa.Column("inserted_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("skipped_duplicate_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("checkpoint_before", sa.JSON(), nullable=True),
        sa.Column("checkpoint_after", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "fetched_count >= 0", name=op.f("ck_ingest_run_fetched_count_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["jurisdiction_code"],
            ["jurisdiction.code"],
            name=op.f("fk_ingest_run_jurisdiction_code_jurisdiction"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingest_run")),
    )
    with op.batch_alter_table("ingest_run", schema=None) as batch_op:
        batch_op.create_index(
            "ix_ingest_run_jurisdiction_code_started_at",
            ["jurisdiction_code", "started_at"],
            unique=False,
        )

    op.create_table(
        "case",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("jurisdiction_code", sa.String(length=8), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("court_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("decision_date", sa.Date(), nullable=True),
        sa.Column("filing_date", sa.Date(), nullable=True),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("case_numbers", sa.JSON(), nullable=False),
        sa.Column("language", sa.String(length=64), nullable=True),
        sa.Column(
            "law_domain",
            sa.Enum(
                "public",
                "private",
                "criminal",
                "other",
                name="law_domain",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("law_subfield", sa.String(length=255), nullable=True),
        sa.Column("procedure_type", sa.String(length=255), nullable=True),
        sa.Column("outcome", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["court_id"], ["court.id"], name=op.f("fk_case_court_id_court"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["jurisdiction_code"],
            ["jurisdiction.code"],
            name=op.f("fk_case_jurisdiction_code_jurisdiction"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case")),
        sa.UniqueConstraint(
            "jurisdiction_code", "source_id", name=op.f("uq_case_jurisdiction_code_source_id")
        ),
    )
    with op.batch_alter_table("case", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_case_content_hash"), ["content_hash"], unique=False)
        batch_op.create_index(batch_op.f("ix_case_court_id"), ["court_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_case_decision_date"), ["decision_date"], unique=False)
        batch_op.create_index(batch_op.f("ix_case_is_published"), ["is_published"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_case_jurisdiction_code"), ["jurisdiction_code"], unique=False
        )
        batch_op.create_index(
            "ix_case_jurisdiction_code_decision_date",
            ["jurisdiction_code", "decision_date"],
            unique=False,
        )
        batch_op.create_index(batch_op.f("ix_case_language"), ["language"], unique=False)

    op.create_table(
        "case_document",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=64), nullable=True),
        sa.Column(
            "doc_type",
            sa.Enum(
                "judgment",
                "opinion",
                "summary",
                "attachment",
                "other",
                name="document_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("format", sa.String(length=64), nullable=True),
        sa.Column("full_text", sa.Text(), nullable=True),
        sa.Column("raw_payload", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["case.id"], name=op.f("fk_case_document_case_id_case"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_document")),
    )
    with op.batch_alter_table("case_document", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_case_document_case_id"), ["case_id"], unique=False)
        batch_op.create_index(
            "ix_case_document_case_id_language", ["case_id", "language"], unique=False
        )

    op.create_table(
        "case_topic",
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "assigned_by",
            sa.Enum(
                "pipeline",
                "manual",
                name="topic_assignment_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["case.id"], name=op.f("fk_case_topic_case_id_case"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topic.id"],
            name=op.f("fk_case_topic_topic_id_topic"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("case_id", "topic_id", name=op.f("pk_case_topic")),
    )
    with op.batch_alter_table("case_topic", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_case_topic_topic_id"), ["topic_id"], unique=False)

    op.create_table(
        "citation",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("target_identifier", sa.String(length=255), nullable=False),
        sa.Column("target_scheme", sa.String(length=64), nullable=True),
        sa.Column("citation_type", sa.String(length=64), nullable=False),
        sa.Column("target_title", sa.Text(), nullable=True),
        sa.Column("target_url", sa.String(length=2048), nullable=True),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["case.id"], name=op.f("fk_citation_case_id_case"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_citation")),
        sa.UniqueConstraint(
            "case_id",
            "target_identifier",
            "citation_type",
            name=op.f("uq_citation_case_id_target_identifier_citation_type"),
        ),
    )
    with op.batch_alter_table("citation", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_citation_case_id"), ["case_id"], unique=False)
        batch_op.create_index("ix_citation_target_identifier", ["target_identifier"], unique=False)

    op.create_table(
        "keyword_match",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("term_id", sa.String(length=255), nullable=False),
        sa.Column("term", sa.String(length=255), nullable=True),
        sa.Column("list_version", sa.String(length=64), nullable=True),
        sa.Column("field", sa.String(length=64), nullable=True),
        sa.Column("weight_applied", sa.Float(), nullable=True),
        sa.Column("match_count", sa.Integer(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["case.id"], name=op.f("fk_keyword_match_case_id_case"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_keyword_match")),
    )
    with op.batch_alter_table("keyword_match", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_keyword_match_case_id"), ["case_id"], unique=False)
        batch_op.create_index(
            "ix_keyword_match_term_id_list_version", ["term_id", "list_version"], unique=False
        )

    op.create_table(
        "party",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "applicant",
                "defendant",
                "intervener",
                "other",
                name="party_role",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("party_type", sa.String(length=64), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=True),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["case.id"], name=op.f("fk_party_case_id_case"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_party")),
    )
    with op.batch_alter_table("party", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_party_case_id"), ["case_id"], unique=False)


def downgrade() -> None:
    """Revert this revision."""
    with op.batch_alter_table("party", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_party_case_id"))

    op.drop_table("party")
    with op.batch_alter_table("keyword_match", schema=None) as batch_op:
        batch_op.drop_index("ix_keyword_match_term_id_list_version")
        batch_op.drop_index(batch_op.f("ix_keyword_match_case_id"))

    op.drop_table("keyword_match")
    with op.batch_alter_table("citation", schema=None) as batch_op:
        batch_op.drop_index("ix_citation_target_identifier")
        batch_op.drop_index(batch_op.f("ix_citation_case_id"))

    op.drop_table("citation")
    with op.batch_alter_table("case_topic", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_case_topic_topic_id"))

    op.drop_table("case_topic")
    with op.batch_alter_table("case_document", schema=None) as batch_op:
        batch_op.drop_index("ix_case_document_case_id_language")
        batch_op.drop_index(batch_op.f("ix_case_document_case_id"))

    op.drop_table("case_document")
    with op.batch_alter_table("case", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_case_language"))
        batch_op.drop_index("ix_case_jurisdiction_code_decision_date")
        batch_op.drop_index(batch_op.f("ix_case_jurisdiction_code"))
        batch_op.drop_index(batch_op.f("ix_case_is_published"))
        batch_op.drop_index(batch_op.f("ix_case_decision_date"))
        batch_op.drop_index(batch_op.f("ix_case_court_id"))
        batch_op.drop_index(batch_op.f("ix_case_content_hash"))

    op.drop_table("case")
    with op.batch_alter_table("ingest_run", schema=None) as batch_op:
        batch_op.drop_index("ix_ingest_run_jurisdiction_code_started_at")

    op.drop_table("ingest_run")
    op.drop_table("ingest_checkpoint")
    with op.batch_alter_table("court", schema=None) as batch_op:
        batch_op.drop_index("ix_court_jurisdiction_code")

    op.drop_table("court")
    with op.batch_alter_table("topic", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_topic_parent_id"))

    op.drop_table("topic")
    op.drop_table("jurisdiction")
