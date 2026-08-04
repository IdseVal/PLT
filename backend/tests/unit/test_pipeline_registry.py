"""Connector discovery: what "adding a jurisdiction is one class and one list" costs.

If the registry needed editing too, onboarding would be three files rather than two, and the
third would be the one everyone forgets. These tests pin that a connector module is found on
its own, and that a jurisdiction claimed twice is an error rather than a coin toss.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from plt.pipeline import registry
from plt.pipeline.base import ConnectorNotFoundError, SourceConnector
from tests.conftest import build_settings
from tests.fakes import FakeConnector


@pytest.fixture(autouse=True)
def clean_registry() -> Iterator[None]:
    """Give every test its own registry, so a fake never leaks into another test."""
    registry.reset_registry()
    yield
    registry.reset_registry()


class OtherFake(FakeConnector):
    """A second connector claiming the same jurisdiction as :class:`FakeConnector`."""

    name = "other-fake"


class Nameless(FakeConnector):
    """A connector that forgot to name itself."""

    name = ""


def test_the_shipped_connector_package_is_scanned_without_error() -> None:
    assert isinstance(registry.connector_classes(), dict)


def test_a_registered_connector_is_found() -> None:
    registry.register_connector(FakeConnector)

    assert registry.available_jurisdictions() == ("NL",)
    assert isinstance(registry.connector_for("nl"), FakeConnector)


def test_a_connector_is_built_with_the_settings_it_is_given() -> None:
    registry.register_connector(FakeConnector)
    settings = build_settings()

    connector = registry.connector_for("NL", settings)

    assert connector.settings is settings


def test_registering_the_same_class_twice_is_harmless() -> None:
    registry.register_connector(FakeConnector)
    registry.register_connector(FakeConnector)

    assert registry.available_jurisdictions() == ("NL",)


def test_two_connectors_for_one_jurisdiction_are_refused() -> None:
    registry.register_connector(FakeConnector)

    with pytest.raises(ValueError, match="two connectors claim jurisdiction NL"):
        registry.register_connector(OtherFake)


def test_a_connector_without_a_name_is_refused() -> None:
    with pytest.raises(TypeError, match="non-empty name"):
        registry.register_connector(Nameless)


def test_an_unknown_jurisdiction_names_what_is_known() -> None:
    registry.register_connector(FakeConnector)

    with pytest.raises(ConnectorNotFoundError, match="Known jurisdictions: NL"):
        registry.connector_for("BE")


def test_the_registry_survives_a_reset() -> None:
    registry.register_connector(FakeConnector)
    registry.reset_registry()

    assert "NL" not in registry.connector_classes() or issubclass(
        registry.connector_classes()["NL"], SourceConnector
    )
