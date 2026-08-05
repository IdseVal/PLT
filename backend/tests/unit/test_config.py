"""Tests for :mod:`plt.config`."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from plt.config import AppEnv, Settings, get_settings
from tests.conftest import REPO_ROOT, build_settings

#: The template a new developer copies to ``backend/.env``.
ENV_EXAMPLE: Path = REPO_ROOT / "backend" / ".env.example"

#: A ``PLT_*`` assignment in ``.env.example``, whether live or commented out as an override.
_ASSIGNMENT = re.compile(r"^#?\s*(PLT_[A-Z0-9_]+)=(.*)$")


def _settings(**overrides: Any) -> Settings:  # noqa: ANN401 - arbitrary field overrides
    return build_settings(**overrides)


def _settings_from(env_file: Path) -> Settings:
    """Load settings from an env file, as the application does at startup.

    The loader options are passed through a mapping because pydantic's
    ``dataclass_transform`` signature does not expose them to a type checker.
    """
    params: dict[str, Any] = {"_env_file": env_file}
    return Settings(**params)


def _documented_assignments() -> list[tuple[str, str]]:
    """Return every assignment in ``.env.example`` as ``(name, dotenv line)``.

    The line is returned verbatim, minus any leading comment marker, so the quoting a
    developer would copy is the quoting under test. Commented-out overrides are included:
    they are documented values too, and one that cannot be loaded is the same defect found
    a week later.
    """
    assignments: list[tuple[str, str]] = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = _ASSIGNMENT.match(line)
        if match is not None:
            assignments.append((match.group(1), f"{match.group(1)}={match.group(2)}"))
    return assignments


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every ``PLT_*`` variable so a developer's own shell cannot change a result.

    An environment variable outranks the dotenv source in pydantic-settings, so without this
    a value exported locally would silently stand in for the one under test.
    """
    for name in [key for key in os.environ if key.upper().startswith("PLT_")]:
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_development_safe() -> None:
    settings = _settings()

    assert settings.page_size_default <= settings.page_size_max
    assert settings.latest_limit_max == 50
    assert settings.rate_limit_enabled is True
    assert settings.api_prefix == "/api"


def test_cors_origins_accept_a_comma_separated_string() -> None:
    settings = _settings(cors_allowed_origins="https://a.example, https://b.example")

    assert settings.cors_allowed_origins == ["https://a.example", "https://b.example"]


def test_cors_origins_accept_a_json_array() -> None:
    settings = _settings(cors_allowed_origins='["https://a.example", "https://b.example"]')

    assert settings.cors_allowed_origins == ["https://a.example", "https://b.example"]


def test_a_malformed_json_array_is_reported_as_a_validation_error() -> None:
    with pytest.raises(ValidationError, match="comma-separated list or a JSON array"):
        _settings(cors_allowed_origins='["https://a.example"')


class TestEnvExample:
    """``backend/.env.example`` must be copyable to ``.env`` and start the application.

    ``test_layout.py`` checks that every setting is *named* in the file. That is not enough:
    pydantic-settings JSON-decodes a complex-typed value inside the dotenv source, before any
    field validator runs, so a documented value can be present, correct-looking and still
    raise ``SettingsError`` the moment the file is loaded. These tests load it.
    """

    @pytest.mark.usefixtures("clean_environment")
    def test_the_file_loads_as_a_valid_configuration(self) -> None:
        settings = _settings_from(ENV_EXAMPLE)

        assert settings.app_env is AppEnv.DEVELOPMENT
        assert settings.api_prefix == "/api"
        assert settings.cors_allowed_origins == ["http://localhost:5173"]
        assert settings.eurlex_languages == ["ENG"]
        assert settings.eurlex_resource_types == ["JUDG", "ORDER", "OPIN_AG", "JUDG_EXTRACT"]

    @pytest.mark.usefixtures("clean_environment")
    @pytest.mark.parametrize(("name", "assignment"), _documented_assignments())
    def test_every_documented_value_loads_as_written(
        self, tmp_path: Path, name: str, assignment: str
    ) -> None:
        """Each documented value, written to a dotenv file on its own, must load.

        Applying it through a file rather than a constructor argument is the point: the
        source-level decoding that broke the list settings only happens on the dotenv and
        environment sources, so a constructor argument would pass either way.
        """
        env_file = tmp_path / ".env"
        env_file.write_text(f"{assignment}\n", encoding="utf-8")

        assert _settings_from(env_file).model_dump() is not None, f"{name} is unloadable"


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
