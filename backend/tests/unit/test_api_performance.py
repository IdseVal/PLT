"""The API answered against a seeded ten-thousand-case database.

The acceptance criteria for issue #10 ask for two properties that only show up at size:
response times that stay acceptable on a corpus of ten thousand cases, and an export of
that corpus that does not assemble its body in memory. The budgets below are deliberately
loose - they are there to catch an accidental full-table scan per row or a materialised
result set, not to police milliseconds on a developer's laptop.

The corpus is built once for the module: ten thousand cases across two jurisdictions and
five courts, with a full-text document on every tenth case.
"""

from __future__ import annotations

import time
import tracemalloc
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from http import HTTPStatus
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import event, insert
from sqlalchemy.engine import Engine

from plt.api.schemas import export_row
from plt.app import create_app
from plt.db.base import Base
from plt.db.models import (
    Case,
    CaseDocument,
    Court,
    DocumentType,
    Jurisdiction,
    JurisdictionType,
    LawDomain,
)
from plt.db.session import create_session_factory, make_engine
from plt.extensions import dispose_database
from tests.conftest import build_settings

#: Size of the seeded corpus, as the acceptance criterion states it.
CASE_COUNT = 10_000

#: Ceiling for a single request against the seeded corpus, in seconds.
BUDGET_SECONDS = 2.0

#: Ceiling on the memory an export of the whole corpus may allocate, in bytes. The rendered
#: body is well over a megabyte, so an implementation that buffered it would not fit.
EXPORT_MEMORY_BUDGET = 4 * 1024 * 1024


def _case_rows() -> list[dict[str, Any]]:
    """Build the ten thousand case rows, spread over ten years and two jurisdictions."""
    now = datetime.now(tz=UTC)
    start = date(2015, 1, 1)
    domains = list(LawDomain)
    rows: list[dict[str, Any]] = []
    for index in range(CASE_COUNT):
        jurisdiction = "NL" if index % 3 else "EU"
        source_id = f"ECLI:NL:RVS:2020:{index}" if jurisdiction == "NL" else f"6{index:09d}"
        rows.append(
            {
                "jurisdiction_code": jurisdiction,
                "source_id": source_id,
                "source_system": "rechtspraak" if jurisdiction == "NL" else "cellar",
                "court_id": 1 + index % 5,
                "title": (
                    f"Case {index} concerning glyphosate authorisation"
                    if index % 5 == 0
                    else f"Case {index} concerning a plant protection product"
                ),
                "abstract": f"Summary of decision {index} on residue limits and spray drift.",
                "decision_date": start + timedelta(days=index % 3650),
                "case_numbers": [f"20{index % 20:02d}/{index}"],
                "language": "nl" if jurisdiction == "NL" else "en",
                "law_domain": domains[index % len(domains)],
                "law_subfield": "environmental" if index % 2 else "liability",
                "revision": 1,
                "first_seen_at": now,
                "last_seen_at": now,
                "updated_at": now,
                "source_metadata": {},
                "is_published": True,
            }
        )
    return rows


def _document_rows(first_case_id: int) -> list[dict[str, Any]]:
    """Build a full-text document for every tenth case."""
    now = datetime.now(tz=UTC)
    text = "The authorisation was reviewed near orchards. " * 40
    return [
        {
            "case_id": first_case_id + index,
            "language": "nl",
            "doc_type": DocumentType.JUDGMENT,
            "format": "xml",
            "full_text": f"{text} Reference {index}.",
            "retrieved_at": now,
            "source_metadata": {},
        }
        for index in range(0, CASE_COUNT, 10)
    ]


@pytest.fixture(scope="module")
def large_app(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Flask]:
    """Yield an application backed by a seeded ten-thousand-case database."""
    database = tmp_path_factory.mktemp("perf") / "perf.db"
    settings = build_settings(
        database_url=f"sqlite+pysqlite:///{database}",
        rate_limit_enabled=False,
    )
    engine = make_engine(settings)
    Base.metadata.create_all(engine)

    with create_session_factory(engine)() as session:
        session.add_all(
            [
                Jurisdiction(
                    code="EU",
                    name="European Union",
                    type=JurisdictionType.SUPRANATIONAL,
                    map_feature_id="EU",
                ),
                Jurisdiction(
                    code="NL",
                    name="Netherlands",
                    type=JurisdictionType.STATE,
                    iso_alpha2="NL",
                    map_feature_id="NL",
                ),
            ]
        )
        session.add_all(
            [
                Court(
                    jurisdiction_code="NL",
                    source_identifier=f"court-{index}",
                    name=f"Court {index}",
                )
                for index in range(1, 6)
            ]
        )
        session.flush()
        session.execute(insert(Case), _case_rows())
        first_case_id = session.scalars(Case.__table__.select().limit(1)).first()
        session.execute(insert(CaseDocument), _document_rows(first_case_id or 1))
        session.commit()
    engine.dispose()

    app = create_app(settings)
    try:
        yield app
    finally:
        dispose_database(app)


@pytest.fixture
def large_client(large_app: Flask) -> Iterator[FlaskClient]:
    """Return a test client for the seeded application."""
    with large_app.test_client() as client:
        yield client


def timed(client: FlaskClient, path: str, **query: str | int) -> tuple[dict[str, Any], float]:
    """Perform a GET, asserting success, and return its body with the elapsed seconds."""
    started = time.perf_counter()
    response = client.get(path, query_string=query)
    elapsed = time.perf_counter() - started

    assert response.status_code == HTTPStatus.OK, response.get_data(as_text=True)
    body = response.get_json()
    return body, elapsed


def report(capsys: pytest.CaptureFixture[str], label: str, elapsed: float) -> None:
    """Print a measurement so the run records the number, not only the verdict."""
    with capsys.disabled():
        print(f"\n{label}: {elapsed * 1000:.0f} ms")  # noqa: T201 - the measurement is the point


class TestResponseTimes:
    """Each read endpoint, against ten thousand cases."""

    def test_the_corpus_is_the_size_the_criterion_names(self, large_client: FlaskClient) -> None:
        body, _ = timed(large_client, "/api/cases")

        assert body["total"] == CASE_COUNT

    def test_first_page(
        self, large_client: FlaskClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        body, elapsed = timed(large_client, "/api/cases")

        report(capsys, "10k cases, first page", elapsed)
        assert len(body["items"]) == 20
        assert elapsed < BUDGET_SECONDS

    def test_deep_page(self, large_client: FlaskClient, capsys: pytest.CaptureFixture[str]) -> None:
        body, elapsed = timed(large_client, "/api/cases", page=400, page_size=25)

        report(capsys, "10k cases, page 400", elapsed)
        assert len(body["items"]) == 25
        assert elapsed < BUDGET_SECONDS

    def test_full_text_search(
        self, large_client: FlaskClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        body, elapsed = timed(large_client, "/api/cases", q="orchards")

        report(capsys, "10k cases, full-text search", elapsed)
        assert body["total"] == CASE_COUNT // 10
        assert elapsed < BUDGET_SECONDS

    def test_relevance_sort(
        self, large_client: FlaskClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        body, elapsed = timed(large_client, "/api/cases", q="glyphosate", sort="relevance")

        report(capsys, "10k cases, relevance sort", elapsed)
        assert body["total"] == CASE_COUNT // 5
        assert elapsed < BUDGET_SECONDS

    def test_filtered_search(
        self, large_client: FlaskClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        body, elapsed = timed(
            large_client,
            "/api/cases",
            jurisdiction="NL",
            law_domain="public",
            date_from="2018-01-01",
            date_to="2022-12-31",
        )

        report(capsys, "10k cases, four filters", elapsed)
        assert body["total"] > 0
        assert elapsed < BUDGET_SECONDS

    def test_latest(self, large_client: FlaskClient, capsys: pytest.CaptureFixture[str]) -> None:
        body, elapsed = timed(large_client, "/api/cases/latest", limit=50)

        report(capsys, "10k cases, latest feed", elapsed)
        assert len(body["items"]) == 50
        assert elapsed < BUDGET_SECONDS

    def test_filters(self, large_client: FlaskClient, capsys: pytest.CaptureFixture[str]) -> None:
        body, elapsed = timed(large_client, "/api/filters")

        report(capsys, "10k cases, facets", elapsed)
        assert len(body["courts"]) == 5
        assert elapsed < BUDGET_SECONDS

    def test_detail(self, large_client: FlaskClient, capsys: pytest.CaptureFixture[str]) -> None:
        body, elapsed = timed(large_client, "/api/cases/NL/ECLI:NL:RVS:2020:5000")

        report(capsys, "10k cases, one case detail", elapsed)
        assert body["source_id"] == "ECLI:NL:RVS:2020:5000"
        assert elapsed < BUDGET_SECONDS


class TestMapEndpoint:
    """The map payload must stay one aggregate query at any corpus size."""

    def test_answers_in_a_single_query(
        self, large_client: FlaskClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        statements: list[str] = []

        def record(*args: Any) -> None:  # noqa: ANN401 - the listener signature is SQLAlchemy's
            statements.append(args[2])

        event.listen(Engine, "before_cursor_execute", record)
        try:
            started = time.perf_counter()
            response = large_client.get("/api/stats/jurisdictions")
            elapsed = time.perf_counter() - started
        finally:
            event.remove(Engine, "before_cursor_execute", record)

        report(capsys, "10k cases, map payload", elapsed)
        payload = response.get_json()
        assert response.status_code == HTTPStatus.OK
        assert {entry["code"] for entry in payload} == {"EU", "NL"}
        assert sum(entry["case_count"] for entry in payload) == CASE_COUNT
        assert len([sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]) == 1
        assert elapsed < BUDGET_SECONDS


class TestExportMemory:
    """A ten-thousand-row export is produced in batches, never assembled."""

    def test_streams_the_whole_corpus_within_a_memory_budget(
        self, large_client: FlaskClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        response = large_client.get("/api/cases/export")
        assert response.status_code == HTTPStatus.OK
        assert response.is_streamed

        tracemalloc.start()
        started = time.perf_counter()
        try:
            lines = 0
            body_bytes = 0
            for chunk in response.iter_encoded():
                lines += 1
                body_bytes += len(chunk)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
            response.close()
        elapsed = time.perf_counter() - started

        with capsys.disabled():
            print(  # noqa: T201 - the measurement is the point of the test
                f"\n10k-row export: {elapsed * 1000:.0f} ms, "
                f"{body_bytes / 1024:.0f} KiB body, {peak / 1024:.0f} KiB peak"
            )
        assert lines == CASE_COUNT + 1
        assert body_bytes > 1_000_000
        assert peak < EXPORT_MEMORY_BUDGET

    def test_only_a_batch_is_rendered_before_the_client_reads(
        self, large_client: FlaskClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rendered: list[int] = []

        def counting_export_row(*args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401 - wraps a function whose arguments are not this test's business
            row = export_row(*args, **kwargs)
            rendered.append(row["id"])
            return row

        monkeypatch.setattr("plt.api.cases.export_row", counting_export_row)

        response = large_client.get("/api/cases/export")
        chunks = response.iter_encoded()
        for _ in range(10):
            next(chunks)
        response.close()

        # Ten lines consumed: a buffering implementation would have rendered all ten
        # thousand rows before the first byte reached the client.
        assert len(rendered) < CASE_COUNT // 10
