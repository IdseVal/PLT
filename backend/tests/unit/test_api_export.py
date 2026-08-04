"""``GET /api/cases/export``: content, streaming and spreadsheet safety.

The export is the one endpoint that can be asked for the whole corpus, so two properties
matter beyond its content: it must produce its body incrementally rather than assembling it
in memory (architecture rule 2.3), and a cell taken from a public source must not become a
formula when the file is opened in a spreadsheet.
"""

from __future__ import annotations

import csv
import io
import json
from http import HTTPStatus
from typing import Any

import pytest
from flask.testing import FlaskClient
from sqlalchemy.orm import Session

from plt.api.schemas import EXPORT_COLUMNS, csv_cell, export_row
from plt.db.models import Case


def csv_rows(client: FlaskClient, **query: str | int) -> list[dict[str, str]]:
    """Perform an export and parse the CSV body into records."""
    response = client.get("/api/cases/export", query_string=query)
    assert response.status_code == HTTPStatus.OK, response.get_data(as_text=True)
    return list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))


def jsonl_records(client: FlaskClient, **query: str | int) -> list[dict[str, Any]]:
    """Perform a JSON-Lines export and parse the body into records."""
    query = {"format": "jsonl", **query}
    response = client.get("/api/cases/export", query_string=query)
    assert response.status_code == HTTPStatus.OK, response.get_data(as_text=True)
    body = response.get_data(as_text=True)
    return [json.loads(line) for line in body.splitlines() if line]


class TestCsvExport:
    """The default CSV serialisation."""

    def test_header_matches_the_documented_columns(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        response = client.get("/api/cases/export")
        header = response.get_data(as_text=True).splitlines()[0]

        assert header.split(",") == list(EXPORT_COLUMNS)

    def test_exports_every_published_case(self, client: FlaskClient, api_corpus: Session) -> None:
        rows = csv_rows(client)

        assert len(rows) == 4
        assert "ECLI:NL:RVS:2025:9" not in {row["source_id"] for row in rows}

    def test_rows_carry_the_resolved_names(self, client: FlaskClient, api_corpus: Session) -> None:
        rows = {row["source_id"]: row for row in csv_rows(client)}
        licence = rows["ECLI:NL:RVS:2024:1"]

        assert licence["jurisdiction_name"] == "Netherlands"
        assert licence["court_name"] == "Raad van State"
        assert licence["decision_date"] == "2024-05-01"

    def test_takes_the_same_filters_as_the_list_endpoint(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        rows = csv_rows(client, jurisdiction="EU")

        assert [row["source_id"] for row in rows] == ["62019CJ0616"]

    def test_is_offered_as_a_download(self, client: FlaskClient, api_corpus: Session) -> None:
        response = client.get("/api/cases/export")

        assert response.mimetype == "text/csv"
        assert response.headers["Content-Disposition"].startswith("attachment; filename=")
        assert response.headers["Content-Disposition"].endswith('.csv"')
        assert response.headers["Cache-Control"] == "no-store"

    def test_never_exposes_the_raw_source_payload(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        assert "secret payload" not in client.get("/api/cases/export").get_data(as_text=True)


class TestSpreadsheetSafety:
    """A cell from a public source must not execute when the file is opened."""

    @pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
    def test_a_formula_cell_is_neutralised(self, prefix: str) -> None:
        assert csv_cell(f"{prefix}cmd|'/c calc'!A1").startswith("'")

    def test_an_ordinary_cell_is_untouched(self) -> None:
        assert csv_cell("Glyphosate licence review") == "Glyphosate licence review"
        assert csv_cell(None) == ""

    def test_a_hostile_title_reaches_the_file_defused(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        api_corpus.add(
            Case(
                jurisdiction_code="NL",
                source_id="ECLI:NL:RVS:2024:7",
                source_system="rechtspraak",
                title='=HYPERLINK("http://evil.invalid","click")',
            )
        )
        api_corpus.commit()

        rows = {row["source_id"]: row for row in csv_rows(client)}

        assert rows["ECLI:NL:RVS:2024:7"]["title"].startswith("'=")


class TestJsonLinesExport:
    """The ``format=jsonl`` serialisation."""

    def test_first_record_describes_the_export(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        records = jsonl_records(client, jurisdiction="EU", sort="date_asc")
        metadata = records[0]

        assert metadata["record_type"] == "metadata"
        assert metadata["filters"]["jurisdiction"] == ["EU"]
        assert metadata["filters"]["sort"] == "date_asc"
        assert metadata["generated_at"].startswith("20")

    def test_every_later_record_is_one_case(self, client: FlaskClient, api_corpus: Session) -> None:
        records = jsonl_records(client)
        cases = records[1:]

        assert len(cases) == 4
        assert {record["record_type"] for record in cases} == {"case"}
        assert set(EXPORT_COLUMNS) <= set(cases[0])

    def test_uses_the_ndjson_media_type(self, client: FlaskClient, api_corpus: Session) -> None:
        response = client.get("/api/cases/export?format=jsonl")

        assert response.mimetype == "application/x-ndjson"
        assert response.headers["Content-Disposition"].endswith('.jsonl"')


class TestStreaming:
    """The body is produced incrementally, not assembled and then sent."""

    def test_response_is_streamed(self, client: FlaskClient, api_corpus: Session) -> None:
        response = client.get("/api/cases/export")

        assert response.is_streamed
        response.close()

    def test_rows_are_rendered_only_as_they_are_consumed(
        self, client: FlaskClient, api_corpus: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rendered: list[int] = []

        def counting_export_row(*args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401 - wraps a function whose arguments are not this test's business
            row = export_row(*args, **kwargs)
            rendered.append(row["id"])
            return row

        monkeypatch.setattr("plt.api.cases.export_row", counting_export_row)

        response = client.get("/api/cases/export")
        chunks = response.iter_encoded()
        next(chunks)  # the header line, before any row has been rendered

        assert rendered == []

        next(chunks)
        assert len(rendered) == 1

        response.close()
