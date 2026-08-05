"""``plt jurisdictions``: the list a scheduler builds its per-jurisdiction jobs from.

The weekly workflow reads this rather than naming NL and EU in a YAML file, so onboarding a
jurisdiction stays one connector plus one keyword list. The JSON shape is therefore part of
the contract with ``.github/workflows/weekly-ingest.yml`` and is asserted, not assumed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from plt.cli import main
from plt.config import Settings
from plt.pipeline import registry
from tests.conftest import build_settings
from tests.fakes import FakeConnector, documents


class NlConnector(FakeConnector):
    """A registered NL connector."""

    jurisdiction_code = "NL"
    name = "fake-nl"

    def __init__(self, settings: Settings | None = None) -> None:
        """Publish nothing; only the registration matters here."""
        super().__init__(settings, docs=documents(1))


class EuConnector(FakeConnector):
    """A registered EU connector."""

    jurisdiction_code = "EU"
    name = "fake-eu"

    def __init__(self, settings: Settings | None = None) -> None:
        """Publish nothing; only the registration matters here."""
        super().__init__(settings, docs=documents(1))


@pytest.fixture
def cli_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the command away from a developer's ``.env`` and the shipped connectors."""
    monkeypatch.setattr("plt.cli.get_settings", lambda: build_settings())
    registry.reset_registry(EuConnector, NlConnector)
    try:
        yield
    finally:
        registry.reset_registry()


def test_the_codes_are_listed_with_their_connectors(
    cli_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    del cli_settings

    assert main(["jurisdictions"]) == 0

    assert capsys.readouterr().out.splitlines() == ["EU\tfake-eu", "NL\tfake-nl"]


def test_json_emits_a_sorted_array_a_workflow_matrix_can_read(
    cli_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    del cli_settings

    assert main(["jurisdictions", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == ["EU", "NL"]


def test_an_empty_registry_is_an_error_rather_than_an_empty_list(
    cli_settings: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A scheduler handed ``[]`` would run nothing and still report success."""
    del cli_settings
    # Emptied through the CLI's own reference: resetting the registry makes it rediscover
    # the shipped connectors, which is the opposite of what this asserts.
    monkeypatch.setattr("plt.cli.connector_classes", dict)

    assert main(["jurisdictions", "--json"]) == 1

    assert "no connectors are registered" in capsys.readouterr().err
