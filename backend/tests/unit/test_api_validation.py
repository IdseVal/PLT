"""Server-side validation and bounding of every query parameter.

Architecture section 5 requires each parameter to be validated and bounded before it
reaches a query, and the issue's acceptance criteria require an out-of-range value to be
*rejected* rather than silently coerced - a client that asked for 5000 rows must be told it
did not get them, instead of receiving 100 and believing it saw everything.
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest
from flask.testing import FlaskClient
from sqlalchemy.orm import Session

from plt.app import create_app
from plt.db.base import Base
from plt.db.session import make_engine
from plt.extensions import dispose_database
from tests.conftest import build_settings


def reject(client: FlaskClient, path: str, **query: str | int) -> dict[str, Any]:
    """Perform a GET expected to fail validation and return the error member."""
    response = client.get(path, query_string=query)
    assert response.status_code == HTTPStatus.BAD_REQUEST, response.get_data(as_text=True)
    body = response.get_json()
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == "validation_error"
    error: dict[str, Any] = body["error"]
    return error


class TestPaginationBounds:
    """``page``, ``page_size`` and ``limit``."""

    def test_page_size_above_the_maximum_is_rejected(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        error = reject(client, "/api/cases", page_size=101)

        assert error["details"]["parameter"] == "page_size"
        assert error["details"]["maximum"] == 100

    def test_page_size_at_the_maximum_is_accepted(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        response = client.get("/api/cases?page_size=100")

        assert response.status_code == HTTPStatus.OK
        assert response.get_json()["page_size"] == 100

    def test_page_size_is_not_silently_coerced(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        response = client.get("/api/cases?page_size=5000")

        assert response.status_code == HTTPStatus.BAD_REQUEST

    @pytest.mark.parametrize("value", [0, -1])
    def test_page_size_below_one_is_rejected(
        self, client: FlaskClient, api_corpus: Session, value: int
    ) -> None:
        reject(client, "/api/cases", page_size=value)

    @pytest.mark.parametrize("value", ["0", "-3", "abc", "1.5", "1e3", "1_0", "٤", "9" * 5000])
    def test_page_must_be_a_positive_integer(
        self, client: FlaskClient, api_corpus: Session, value: str
    ) -> None:
        reject(client, "/api/cases", page=value)

    def test_limit_above_the_maximum_is_rejected(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        error = reject(client, "/api/cases/latest", limit=51)

        assert error["details"]["parameter"] == "limit"
        assert error["details"]["maximum"] == 50

    def test_limit_at_the_maximum_is_accepted(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        assert client.get("/api/cases/latest?limit=50").status_code == HTTPStatus.OK

    def test_bounds_follow_configuration_rather_than_a_literal(self, tmp_path: Path) -> None:
        settings = build_settings(
            database_url=f"sqlite+pysqlite:///{tmp_path / 'bounds.db'}",
            rate_limit_enabled=False,
            page_size_default=5,
            page_size_max=10,
            latest_limit_default=3,
            latest_limit_max=4,
        )
        Base.metadata.create_all(make_engine(settings))
        app = create_app(settings)
        try:
            with app.test_client() as client:
                assert client.get("/api/cases?page_size=10").status_code == HTTPStatus.OK
                assert client.get("/api/cases?page_size=11").status_code == HTTPStatus.BAD_REQUEST
                assert client.get("/api/cases/latest?limit=5").status_code == HTTPStatus.BAD_REQUEST
        finally:
            dispose_database(app)


class TestFilterValidation:
    """Enumerations, dates, codes and free text."""

    @pytest.mark.parametrize("value", ["dates", "date_desc;", "DATE_DESC ASC", "relevance2"])
    def test_unknown_sort_is_rejected(
        self, client: FlaskClient, api_corpus: Session, value: str
    ) -> None:
        error = reject(client, "/api/cases", sort=value)

        assert error["details"]["parameter"] == "sort"

    def test_known_sort_is_case_insensitive(self, client: FlaskClient, api_corpus: Session) -> None:
        assert client.get("/api/cases?sort=DATE_ASC").status_code == HTTPStatus.OK

    def test_unknown_law_domain_is_rejected(self, client: FlaskClient, api_corpus: Session) -> None:
        reject(client, "/api/cases", law_domain="tax")

    @pytest.mark.parametrize("value", ["01-01-2024", "2024/01/01", "2024-13-01", "yesterday", "0"])
    def test_malformed_date_is_rejected(
        self, client: FlaskClient, api_corpus: Session, value: str
    ) -> None:
        reject(client, "/api/cases", date_from=value)

    def test_inverted_date_range_is_rejected(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        reject(client, "/api/cases", date_from="2024-05-01", date_to="2023-01-01")

    @pytest.mark.parametrize("value", ["N", "NETHERLANDS-1", "N;", "../nl", "NL'"])
    def test_malformed_jurisdiction_code_is_rejected(
        self, client: FlaskClient, api_corpus: Session, value: str
    ) -> None:
        reject(client, "/api/cases", jurisdiction=value)

    def test_too_many_jurisdictions_are_rejected(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        codes = ",".join(f"J{index:02d}" for index in range(40))

        reject(client, "/api/cases", jurisdiction=codes)

    @pytest.mark.parametrize("value", ["Spray Drift", "spray drift", "spray_drift", "spray--"])
    def test_malformed_topic_slug_is_rejected(
        self, client: FlaskClient, api_corpus: Session, value: str
    ) -> None:
        reject(client, "/api/cases", topic=value)

    @pytest.mark.parametrize("value", ["dutch!", "n", "nederlands-x-y-z-really-long"])
    def test_malformed_language_is_rejected(
        self, client: FlaskClient, api_corpus: Session, value: str
    ) -> None:
        reject(client, "/api/cases", language=value)

    @pytest.mark.parametrize("value", ["0", "-1", "abc", "1;2"])
    def test_malformed_court_is_rejected(
        self, client: FlaskClient, api_corpus: Session, value: str
    ) -> None:
        reject(client, "/api/cases", court=value)

    def test_over_long_search_term_is_rejected(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        error = reject(client, "/api/cases", q="a" * 201)

        assert error["details"]["parameter"] == "q"
        assert "201" not in error["message"]

    def test_search_term_at_the_limit_is_accepted(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        assert client.get("/api/cases", query_string={"q": "a" * 200}).status_code == HTTPStatus.OK

    def test_empty_parameters_fall_back_to_their_defaults(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        response = client.get("/api/cases?q=&page=&page_size=&sort=&jurisdiction=&topic=")

        assert response.status_code == HTTPStatus.OK
        assert response.get_json()["total"] == 4

    def test_unknown_parameters_are_ignored(self, client: FlaskClient, api_corpus: Session) -> None:
        response = client.get("/api/cases?include_unpublished=true&order_by=id")

        assert response.status_code == HTTPStatus.OK
        assert response.get_json()["total"] == 4

    def test_an_error_never_echoes_an_unbounded_value(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        error = reject(client, "/api/cases", page="9" * 500)

        assert len(error["details"]["value"]) <= 105


class TestPathValidation:
    """The case-detail path segments."""

    @pytest.mark.parametrize("code", ["N", "netherlands", "N%20L"])
    def test_malformed_jurisdiction_segment_is_rejected(
        self, client: FlaskClient, api_corpus: Session, code: str
    ) -> None:
        response = client.get(f"/api/cases/{code}/ECLI:NL:RVS:2024:1")

        assert response.status_code == HTTPStatus.BAD_REQUEST

    @pytest.mark.parametrize("source_id", ["'", "<script>", "%00", "a" * 300])
    def test_malformed_source_id_is_rejected(
        self, client: FlaskClient, api_corpus: Session, source_id: str
    ) -> None:
        response = client.get(f"/api/cases/NL/{source_id}")

        assert response.status_code in {HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND}


class TestExportValidation:
    """``/api/cases/export`` shares the case filters and adds ``format``."""

    def test_unknown_format_is_rejected(self, client: FlaskClient, api_corpus: Session) -> None:
        error = reject(client, "/api/cases/export", format="xlsx")

        assert error["details"]["parameter"] == "format"

    def test_filters_are_validated_the_same_way(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        reject(client, "/api/cases/export", date_to="not-a-date")
