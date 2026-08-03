"""Tests for :mod:`plt.config`."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from plt.config import AppEnv, Settings, get_settings
from tests.conftest import build_settings


def _settings(**overrides: Any) -> Settings:  # noqa: ANN401 - arbitrary field overrides
    return build_settings(**overrides)


def test_defaults_are_development_safe() -> None:
    settings = _settings()

    assert settings.page_size_default <= settings.page_size_max
    assert settings.latest_limit_max == 50
    assert settings.rate_limit_enabled is True
    assert settings.api_prefix == "/api"


def test_cors_origins_accept_a_comma_separated_string() -> None:
    settings = _settings(cors_allowed_origins="https://a.example, https://b.example")

    assert settings.cors_allowed_origins == ["https://a.example", "https://b.example"]


def test_api_prefix_requires_a_leading_slash() -> None:
    with pytest.raises(ValidationError):
        _settings(api_prefix="api")


def test_log_level_is_normalised_and_validated() -> None:
    assert _settings(log_level="debug").log_level == "DEBUG"
    with pytest.raises(ValidationError):
        _settings(log_level="verbose")


def test_page_size_default_may_not_exceed_the_maximum() -> None:
    with pytest.raises(ValidationError):
        _settings(page_size_default=200, page_size_max=100)


def test_production_refuses_the_placeholder_secret() -> None:
    with pytest.raises(ValidationError):
        build_settings(app_env=AppEnv.PRODUCTION)


def test_production_refuses_debug_mode() -> None:
    with pytest.raises(ValidationError):
        build_settings(
            app_env=AppEnv.PRODUCTION,
            secret_key="a-real-generated-value",
            debug=True,
        )


def test_keyword_list_path_points_at_the_committed_lists() -> None:
    settings = _settings()

    path = settings.keyword_list_path("NL")

    assert path == settings.keywords_dir / "nl.json"
    assert path.is_file(), "data/keywords/nl.json is committed and must be reachable"
    assert settings.keyword_list_path("eu").is_file()


@pytest.mark.parametrize("code", ["../etc", "N", "NLD", "n1", ""])
def test_keyword_list_path_rejects_anything_but_a_two_letter_code(code: str) -> None:
    with pytest.raises(ValueError, match="two ASCII letters"):
        _settings().keyword_list_path(code)


def test_user_agent_names_the_project_and_a_contact() -> None:
    agent = _settings().user_agent("1.2.3")

    assert "PLT/1.2.3" in agent
    assert "@" in agent


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
