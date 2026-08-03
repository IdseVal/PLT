"""The schema is a contract between four work streams, so it is asserted, not assumed.

``docs/architecture.md`` section 3 fixes the tables, their minimum column set and the
deduplication key. These tests fail the moment a column named there disappears, the unique
constraint stops being enforced by the database, or a type creeps in that PostgreSQL cannot
compile.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from plt.db.base import Base, UtcDateTime
from plt.db.models import (
    Case,
    CaseDocument,
    CaseTopic,
    Citation,
    Court,
    DocumentType,
    IngestCheckpoint,
    IngestRun,
    IngestStatus,
    Jurisdiction,
    JurisdictionType,
    KeywordMatch,
    LawDomain,
    Party,
    PartyRole,
    Topic,
)

#: Table -> the columns architecture section 3 requires. Extra columns are allowed.
CONTRACT_COLUMNS: dict[str, tuple[str, ...]] = {
    "jurisdiction": ("code", "name", "type", "iso_alpha2", "map_feature_id", "is_active"),
    "court": ("id", "jurisdiction_code", "source_identifier", "name", "level", "domain"),
    "case": (
        "id",
        "jurisdiction_code",
        "source_id",
        "source_system",
        "court_id",
        "title",
        "abstract",
        "decision_date",
        "filing_date",
        "publication_date",
        "case_numbers",
        "language",
        "law_domain",
        "law_subfield",
        "procedure_type",
        "outcome",
        "source_url",
        "content_hash",
        "first_seen_at",
        "last_seen_at",
        "updated_at",
        "source_metadata",
        "is_published",
    ),
    "case_document": (
        "id",
        "case_id",
        "language",
        "doc_type",
        "format",
        "full_text",
        "raw_payload",
        "retrieved_at",
    ),
    "party": ("id", "case_id", "name", "role", "party_type"),
    "topic": ("id", "slug", "label", "parent_id"),
    "case_topic": ("case_id", "topic_id"),
    "keyword_match": (
        "id",
        "case_id",
        "term_id",
        "list_version",
        "field",
        "weight_applied",
        "snippet",
    ),
    "citation": ("id", "case_id", "target_identifier", "citation_type"),
    "ingest_run": (
        "id",
        "jurisdiction_code",
        "connector",
        "started_at",
        "finished_at",
        "status",
        "fetched_count",
        "matched_count",
        "inserted_count",
        "updated_count",
        "skipped_duplicate_count",
        "error_count",
        "checkpoint_before",
        "checkpoint_after",
    ),
    "ingest_checkpoint": (
        "connector",
        "jurisdiction_code",
        "last_modified_seen",
        "last_cursor",
        "updated_at",
    ),
}

#: Indexes the acceptance criteria of issue #4 require, as (table, column tuple).
REQUIRED_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("case", ("decision_date",)),
    ("case", ("jurisdiction_code",)),
    ("case", ("content_hash",)),
    ("keyword_match", ("case_id",)),
    ("case_document", ("case_id",)),
)


def make_case(session: Session, source_id: str = "ECLI:NL:HR:2026:1", **overrides: object) -> Case:
    """Persist a minimal case in the NL jurisdiction and return it."""
    values: dict[str, object] = {
        "jurisdiction_code": "NL",
        "source_id": source_id,
        "source_system": "rechtspraak",
        "title": "Lelieteelt en gewasbeschermingsmiddelen",
        "decision_date": date(2026, 3, 1),
        "content_hash": "hash-1",
    }
    values.update(overrides)
    case = Case(**values)
    session.add(case)
    session.flush()
    return case


# --------------------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("table_name", "columns"), sorted(CONTRACT_COLUMNS.items()))
def test_contract_tables_have_their_columns(table_name: str, columns: tuple[str, ...]) -> None:
    table = Base.metadata.tables[table_name]
    missing = [name for name in columns if name not in table.columns]

    assert missing == [], f"{table_name} is missing {missing} (architecture section 3)"


@pytest.mark.parametrize(("table_name", "columns"), REQUIRED_INDEXES)
def test_required_indexes_exist(table_name: str, columns: tuple[str, ...]) -> None:
    table = Base.metadata.tables[table_name]
    indexed = {tuple(column.name for column in index.columns) for index in table.indexes}
    covered = any(existing[: len(columns)] == columns for existing in indexed)

    assert covered, f"{table_name}{columns} is not indexed; indexes present: {sorted(indexed)}"


def test_dedup_key_is_a_database_constraint() -> None:
    case_table = Base.metadata.tables["case"]
    unique_columns = {
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in case_table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }

    assert ("jurisdiction_code", "source_id") in unique_columns


def test_schema_compiles_for_postgresql() -> None:
    """Every table must render as PostgreSQL DDL, with portable JSON and aware timestamps."""
    # SQLAlchemy's dialect constructors carry no annotations.
    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    statements = [
        str(sa.schema.CreateTable(table).compile(dialect=dialect))
        for table in Base.metadata.sorted_tables
    ]
    ddl = "\n".join(statements)

    assert "TIMESTAMP WITH TIME ZONE" in ddl
    assert " JSON" in ddl
    assert "JSONB" not in ddl
    # A native ENUM type would have to be created and altered separately on PostgreSQL.
    assert "CREATE TYPE" not in ddl


def test_schema_uses_no_sqlite_only_types() -> None:
    """No column may compile on SQLite to a type PostgreSQL does not have."""
    offenders = [
        f"{table.name}.{column.name}: {column.type}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if column.type.compile(dialect=sqlite.dialect()).upper() in {"BLOB", "NUMERIC"}
    ]

    assert offenders == []


# --------------------------------------------------------------------------------------
# Deduplication
# --------------------------------------------------------------------------------------


def test_duplicate_source_id_in_one_jurisdiction_is_rejected(seeded_session: Session) -> None:
    make_case(seeded_session, "ECLI:NL:RBDHA:2026:1")

    with pytest.raises(IntegrityError):
        make_case(seeded_session, "ECLI:NL:RBDHA:2026:1")


def test_same_source_id_in_another_jurisdiction_is_allowed(seeded_session: Session) -> None:
    make_case(seeded_session, "62026CJ0001")
    make_case(
        seeded_session,
        "62026CJ0001",
        jurisdiction_code="EU",
        source_system="cellar",
    )

    assert seeded_session.scalar(sa.select(sa.func.count(Case.id))) == 2


def test_content_hash_distinguishes_a_revision_from_a_re_fetch(seeded_session: Session) -> None:
    case = make_case(seeded_session, "ECLI:NL:HR:2026:9", content_hash="hash-a")
    first_seen = case.first_seen_at

    case.last_seen_at = datetime.now(UTC)
    case.content_hash = "hash-b"
    case.revision += 1
    seeded_session.flush()

    assert case.revision == 2
    assert case.first_seen_at == first_seen


# --------------------------------------------------------------------------------------
# Cascades
# --------------------------------------------------------------------------------------


def test_orm_delete_cascades_to_children(seeded_session: Session) -> None:
    topic = Topic(slug="drift", label="Spray drift")
    seeded_session.add(topic)
    case = make_case(seeded_session, "ECLI:NL:RBOBR:2026:5")
    case.documents.append(CaseDocument(doc_type=DocumentType.JUDGMENT, full_text="pesticiden"))
    case.parties.append(Party(name="Stichting X", role=PartyRole.APPLICANT))
    case.keyword_matches.append(KeywordMatch(term_id="nl-001", term="lelieteelt"))
    case.citations.append(Citation(target_identifier="32009R1107", citation_type="cites"))
    seeded_session.flush()
    case.topics.append(CaseTopic(topic_id=topic.id))
    seeded_session.flush()

    seeded_session.delete(case)
    seeded_session.flush()

    for model in (CaseDocument, Party, KeywordMatch, Citation, CaseTopic):
        assert seeded_session.scalar(sa.select(sa.func.count()).select_from(model)) == 0
    # The topic itself is reference data and survives its association.
    assert seeded_session.scalar(sa.select(sa.func.count(Topic.id))) == 1


def test_delete_cascades_at_the_database_level(seeded_session: Session) -> None:
    """A Core DELETE bypasses the ORM, so this proves the FK cascade is enforced by SQLite."""
    case = make_case(seeded_session, "ECLI:NL:RBAMS:2026:7")
    case.documents.append(CaseDocument(doc_type=DocumentType.SUMMARY, full_text="samenvatting"))
    seeded_session.flush()
    seeded_session.expunge_all()

    seeded_session.execute(sa.delete(Case).where(Case.source_id == "ECLI:NL:RBAMS:2026:7"))

    assert seeded_session.scalar(sa.select(sa.func.count(CaseDocument.id))) == 0


def test_deleting_a_court_leaves_its_cases(seeded_session: Session) -> None:
    court = Court(jurisdiction_code="NL", source_identifier="rb-den-haag", name="Rechtbank")
    seeded_session.add(court)
    seeded_session.flush()
    case = make_case(seeded_session, "ECLI:NL:RBDHA:2026:8", court_id=court.id)

    seeded_session.execute(sa.delete(Court).where(Court.id == court.id))
    seeded_session.expire(case)

    assert case.court_id is None


# --------------------------------------------------------------------------------------
# Timestamps and enumerations
# --------------------------------------------------------------------------------------


def test_timestamps_come_back_timezone_aware_in_utc(seeded_session: Session) -> None:
    case = make_case(seeded_session, "ECLI:NL:HR:2026:11")
    seeded_session.expire(case)

    assert case.first_seen_at.tzinfo is not None
    assert case.first_seen_at.utcoffset() == timedelta(0)


def test_a_non_utc_timestamp_is_stored_as_utc(seeded_session: Session) -> None:
    berlin = timezone(timedelta(hours=2))
    retrieved = datetime(2026, 5, 1, 12, 0, tzinfo=berlin)
    case = make_case(seeded_session, "ECLI:NL:HR:2026:12")
    document = CaseDocument(case_id=case.id, doc_type=DocumentType.JUDGMENT, retrieved_at=retrieved)
    seeded_session.add(document)
    seeded_session.flush()
    seeded_session.expire(document)

    assert document.retrieved_at == retrieved
    assert document.retrieved_at.utcoffset() == timedelta(0)
    assert document.retrieved_at.hour == 10


def test_the_timestamp_type_handles_null_and_already_aware_values() -> None:
    """PostgreSQL returns aware values and every timestamp column is nullable somewhere."""
    column_type = UtcDateTime()
    dialect = sqlite.dialect()
    aware = datetime(2026, 5, 1, 12, 0, tzinfo=timezone(timedelta(hours=2)))

    assert column_type.process_bind_param(None, dialect) is None
    assert column_type.process_result_value(None, dialect) is None
    assert column_type.process_result_value(aware, dialect) == aware
    assert column_type.process_result_value(aware, dialect).utcoffset() == timedelta(0)  # type: ignore[union-attr]


def test_a_naive_timestamp_is_rejected(seeded_session: Session) -> None:
    case = make_case(seeded_session, "ECLI:NL:HR:2026:13")
    seeded_session.add(
        CaseDocument(
            case_id=case.id,
            doc_type=DocumentType.JUDGMENT,
            retrieved_at=datetime(2026, 5, 1, 12, 0),  # noqa: DTZ001 - the point of the test
        )
    )

    with pytest.raises(StatementError):
        seeded_session.flush()


def test_enum_values_are_checked_by_the_database(seeded_session: Session) -> None:
    """The CHECK constraint must reject a value the ORM never produces."""
    with pytest.raises(IntegrityError):
        seeded_session.execute(
            sa.text(
                "INSERT INTO jurisdiction "
                "(code, name, type, is_active, source_metadata, created_at, updated_at) "
                "VALUES ('XX', 'Nowhere', :bad, 1, '{}', :now, :now)"
            ),
            {"bad": "federation", "now": datetime.now(UTC).isoformat()},
        )


def test_enum_columns_round_trip_as_their_string_value(seeded_session: Session) -> None:
    case = make_case(seeded_session, "ECLI:NL:HR:2026:14", law_domain=LawDomain.PUBLIC)
    seeded_session.expire(case)

    assert case.law_domain is LawDomain.PUBLIC
    stored = seeded_session.execute(
        sa.text("SELECT law_domain FROM 'case' WHERE id = :id"), {"id": case.id}
    ).scalar_one()

    assert stored == "public"


# --------------------------------------------------------------------------------------
# Metadata retention
# --------------------------------------------------------------------------------------


def test_source_metadata_keeps_everything_the_endpoint_exposed(seeded_session: Session) -> None:
    """Fields with no column of their own survive a round trip unchanged."""
    payload = {
        "zaaknummer": ["AWB 25/1234", "AWB 25/1235"],
        "procedure": "Eerste aanleg - meervoudig",
        "vindplaatsen": [{"naam": "JM 2026/45", "annotator": "Van Dijk"}],
        "nested": {"unicode": "gewasbeschermingsmiddelen — €", "count": 3},
    }
    case = make_case(
        seeded_session,
        "ECLI:NL:RVS:2026:20",
        source_metadata=payload,
        case_numbers=["AWB 25/1234"],
    )
    seeded_session.expire(case)

    assert case.source_metadata == payload
    assert case.case_numbers == ["AWB 25/1234"]


def test_raw_payload_is_stored_verbatim(seeded_session: Session) -> None:
    raw = '<?xml version="1.0"?><open-rechtspraak><uitspraak>tekst</uitspraak></open-rechtspraak>'
    case = make_case(seeded_session, "ECLI:NL:RVS:2026:21")
    document = CaseDocument(
        case_id=case.id,
        doc_type=DocumentType.JUDGMENT,
        format="xml",
        raw_payload=raw,
        full_text="tekst",
    )
    seeded_session.add(document)
    seeded_session.flush()
    seeded_session.expire(document)

    assert document.raw_payload == raw


def test_an_ingest_run_records_its_checkpoints(seeded_session: Session) -> None:
    run = IngestRun(
        jurisdiction_code="NL",
        connector="rechtspraak",
        status=IngestStatus.SUCCESS,
        finished_at=datetime.now(UTC),
        fetched_count=10,
        matched_count=3,
        inserted_count=2,
        updated_count=1,
        skipped_duplicate_count=7,
        checkpoint_before={"last_modified_seen": "2026-07-01T00:00:00+00:00"},
        checkpoint_after={"last_modified_seen": "2026-07-08T00:00:00+00:00"},
    )
    seeded_session.add(run)
    seeded_session.flush()
    seeded_session.expire(run)

    assert run.checkpoint_after == {"last_modified_seen": "2026-07-08T00:00:00+00:00"}


def test_a_checkpoint_is_one_row_per_connector(seeded_session: Session) -> None:
    seeded_session.add(
        IngestCheckpoint(
            connector="rechtspraak",
            jurisdiction_code="NL",
            last_modified_seen=datetime.now(UTC),
            last_cursor="offset=100",
        )
    )
    seeded_session.flush()
    seeded_session.add(IngestCheckpoint(connector="rechtspraak", jurisdiction_code="NL"))

    with pytest.raises(IntegrityError):
        seeded_session.flush()


def test_the_seeded_jurisdictions_carry_what_the_map_needs(seeded_session: Session) -> None:
    eu = seeded_session.get(Jurisdiction, "EU")

    assert eu is not None
    assert eu.type is JurisdictionType.SUPRANATIONAL
    assert eu.map_feature_id == "EU"
