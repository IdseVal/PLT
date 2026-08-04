"""The security properties architecture section 5 calls non-optional.

These endpoints are public and unauthenticated, so the rate limiter, the CORS restriction
and the error envelope are load-bearing rather than hygiene: a stack trace, a SQL fragment
or a file path in a response is a disclosure, and an unbounded export is a denial of
service.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from http import HTTPStatus
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy.orm import Session

from plt.app import create_app
from plt.config import Settings
from plt.db.base import Base
from plt.db.session import make_engine
from plt.extensions import dispose_database, limiter
from tests.conftest import build_settings


def build_limited_app(tmp_path: Path, **overrides: str) -> Flask:
    """Build an application with rate limiting switched on and a schema in place."""
    settings: Settings = build_settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'limited.db'}",
        rate_limit_enabled=True,
        **overrides,
    )
    Base.metadata.create_all(make_engine(settings))
    return create_app(settings)


@pytest.fixture
def limited_app(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Flask]:
    """Yield a rate-limited application, resetting the limiter's counters around the test."""
    overrides: dict[str, str] = getattr(request, "param", {})
    app = build_limited_app(tmp_path, **overrides)
    limiter.reset()
    try:
        yield app
    finally:
        limiter.reset()
        dispose_database(app)


class TestRateLimiting:
    """Every endpoint is limited; the export is limited harder."""

    @pytest.mark.parametrize(
        "limited_app",
        [{"rate_limit_default": "2 per minute"}],
        indirect=True,
    )
    @pytest.mark.parametrize(
        "path",
        [
            "/api/health",
            "/api/cases",
            "/api/cases/latest",
            "/api/filters",
            "/api/stats/jurisdictions",
            "/api/cases/NL/ECLI:NL:RVS:2024:1",
        ],
    )
    def test_the_default_limit_applies_to_every_endpoint(
        self, limited_app: Flask, path: str
    ) -> None:
        with limited_app.test_client() as client:
            statuses = [client.get(path).status_code for _ in range(3)]

        assert statuses[-1] == HTTPStatus.TOO_MANY_REQUESTS

    @pytest.mark.parametrize(
        "limited_app",
        [{"rate_limit_default": "2 per minute"}],
        indirect=True,
    )
    def test_the_default_limit_is_a_budget_per_endpoint(self, limited_app: Flask) -> None:
        # Flask-Limiter scopes a default limit to the endpoint it guards, so exhausting one
        # endpoint's budget does not lock a client out of the rest of the API.
        with limited_app.test_client() as client:
            for _ in range(3):
                client.get("/api/cases")
            other = client.get("/api/filters")

        assert other.status_code == HTTPStatus.OK

    @pytest.mark.parametrize(
        "limited_app",
        [{"rate_limit_default": "2 per minute"}],
        indirect=True,
    )
    def test_a_refusal_uses_the_error_envelope(self, limited_app: Flask) -> None:
        with limited_app.test_client() as client:
            for _ in range(3):
                response = client.get("/api/cases")

        body = response.get_json()
        assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
        assert set(body["error"]) == {"code", "message", "details"}
        assert body["error"]["code"] == "too_many_requests"

    @pytest.mark.parametrize(
        "limited_app",
        [{"rate_limit_default": "60 per minute", "rate_limit_export": "2 per hour"}],
        indirect=True,
    )
    def test_the_export_is_limited_more_strictly_than_the_rest(self, limited_app: Flask) -> None:
        with limited_app.test_client() as client:
            first = client.get("/api/cases/export")
            second = client.get("/api/cases/export")
            third = client.get("/api/cases/export")
            after = client.get("/api/cases")

        assert [first.status_code, second.status_code] == [HTTPStatus.OK, HTTPStatus.OK]
        assert third.status_code == HTTPStatus.TOO_MANY_REQUESTS
        # The stricter limit is the export's own: the list endpoint is unaffected.
        assert after.status_code == HTTPStatus.OK

    def test_the_limit_is_configuration_not_a_literal(self, tmp_path: Path) -> None:
        app = build_limited_app(tmp_path, rate_limit_default="1 per minute")
        limiter.reset()
        try:
            with app.test_client() as client:
                assert client.get("/api/filters").status_code == HTTPStatus.OK
                assert client.get("/api/filters").status_code == HTTPStatus.TOO_MANY_REQUESTS
        finally:
            limiter.reset()
            dispose_database(app)


class TestErrorDisclosure:
    """A failure says what went wrong, never how the server is built."""

    def test_an_unexpected_exception_returns_a_generic_envelope(
        self,
        client: FlaskClient,
        api_corpus: Session,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from plt.api import stats as stats_module

        secret = "SELECT password FROM admin WHERE id = 1 -- C:\\srv\\plt\\secrets.env"

        def explode(_session: Session) -> None:
            raise RuntimeError(secret)

        monkeypatch.setattr(stats_module, "jurisdiction_stats", explode)

        with caplog.at_level(logging.ERROR):
            response = client.get("/api/stats/jurisdictions")

        body = response.get_data(as_text=True)
        assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
        assert response.get_json()["error"]["code"] == "internal_error"
        for leak in (secret, "Traceback", "SELECT", "srv", ".py", "RuntimeError"):
            assert leak not in body
        # The detail is not lost, it is logged where only an operator can read it.
        assert any(secret in record.getMessage() or record.exc_info for record in caplog.records)

    def test_a_database_failure_is_not_reported_as_sql(
        self, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No schema exists on this database, so the query fails inside the driver.
        response = client.get("/api/cases")
        body = response.get_data(as_text=True)

        assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
        assert "no such table" not in body
        assert "SELECT" not in body

    def test_an_unsupported_method_uses_the_envelope(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        response = client.post("/api/cases")
        body = response.get_json()

        assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
        assert set(body["error"]) == {"code", "message", "details"}

    def test_a_validation_message_does_not_reflect_markup(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        response = client.get("/api/cases", query_string={"sort": "<script>alert(1)</script>"})
        body = response.get_data(as_text=True)

        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert "<script>" not in body


class TestCors:
    """Cross-origin access is restricted to the configured origins, never ``*``."""

    def test_a_configured_origin_is_echoed(self, client: FlaskClient, api_corpus: Session) -> None:
        response = client.get("/api/cases", headers={"Origin": "http://localhost:5173"})

        assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"

    def test_an_unconfigured_origin_gets_no_grant(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        response = client.get("/api/cases", headers={"Origin": "https://evil.invalid"})

        assert "Access-Control-Allow-Origin" not in response.headers

    def test_the_export_is_not_open_to_every_origin(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        response = client.get("/api/cases/export", headers={"Origin": "https://evil.invalid"})

        assert response.headers.get("Access-Control-Allow-Origin") != "*"
