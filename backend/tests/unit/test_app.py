"""Tests for the application factory and the health endpoint."""

from __future__ import annotations

from http import HTTPStatus

from flask import Flask
from flask.testing import FlaskClient

from plt import __version__
from plt.app import create_app
from tests.conftest import build_settings


def test_create_app_returns_configured_application(app: Flask) -> None:
    assert app.config["TESTING"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True


def test_cli_group_is_registered_on_the_application(app: Flask) -> None:
    assert "plt" in app.cli.commands


def test_health_returns_200_json(client: FlaskClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == HTTPStatus.OK
    assert response.mimetype == "application/json"
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["ingest"] == {}


def test_api_prefix_is_configurable() -> None:
    app = create_app(build_settings(api_prefix="/v1"))

    with app.test_client() as client:
        assert client.get("/v1/health").status_code == HTTPStatus.OK
        assert client.get("/api/health").status_code == HTTPStatus.NOT_FOUND


def test_unknown_route_uses_the_error_envelope(client: FlaskClient) -> None:
    response = client.get("/api/does-not-exist")

    assert response.status_code == HTTPStatus.NOT_FOUND
    body = response.get_json()
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == "not_found"


def test_cors_header_reflects_configured_origin(app: Flask) -> None:
    origin = "http://localhost:5173"
    with app.test_client() as client:
        response = client.get("/api/health", headers={"Origin": origin})

    assert response.headers["Access-Control-Allow-Origin"] == origin


def test_cors_rejects_unconfigured_origin(app: Flask) -> None:
    with app.test_client() as client:
        response = client.get("/api/health", headers={"Origin": "https://evil.example"})

    assert "Access-Control-Allow-Origin" not in response.headers
