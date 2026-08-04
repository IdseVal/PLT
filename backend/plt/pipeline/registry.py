"""Finding the connector that serves a jurisdiction.

"Adding a jurisdiction means writing one connector class and one keyword list, and nothing
else" (``docs/architecture.md`` section 4) is only true if nothing has to be *registered*
either. So the registry discovers connectors instead of listing them: it imports every module
under :mod:`plt.pipeline.connectors` and picks up the concrete
:class:`~plt.pipeline.base.SourceConnector` subclasses it finds, keyed on their
``jurisdiction_code``. Dropping ``connectors/be.py`` into place is the whole of the wiring.

Two connectors claiming the same jurisdiction is an error rather than a silent
last-one-wins: a jurisdiction will eventually have more than one source, and that day the
runner needs a connector *name* rather than a jurisdiction code, which is a deliberate
change to make and not something to fall into by accident.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING

from plt.pipeline import connectors as connectors_package
from plt.pipeline.base import ConnectorNotFoundError, SourceConnector
from plt.utils.logging import get_logger

if TYPE_CHECKING:
    from plt.config import Settings

__all__ = [
    "available_jurisdictions",
    "connector_classes",
    "connector_for",
    "register_connector",
    "reset_registry",
]

log = get_logger(__name__)

#: Connector classes by jurisdiction code, populated on first use and by explicit
#: registration. ``None`` means discovery has not run yet.
_registry: dict[str, type[SourceConnector]] | None = None


def _iter_candidate_classes() -> Iterator[type[SourceConnector]]:
    """Yield every concrete connector class defined under the connectors package.

    A class is only yielded from the module that defines it, so a connector imported into a
    second module for convenience is not counted twice.

    Yields:
        Concrete :class:`~plt.pipeline.base.SourceConnector` subclasses.
    """
    for module_info in pkgutil.iter_modules(connectors_package.__path__):
        module_name = f"{connectors_package.__name__}.{module_info.name}"
        try:
            module = importlib.import_module(module_name)
        except Exception:
            # One broken connector must not make every other jurisdiction unrunnable.
            log.exception("connector module failed to import", extra={"module": module_name})
            continue
        for _, member in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(member, SourceConnector)
                and member is not SourceConnector
                and not inspect.isabstract(member)
                and member.__module__ == module_name
            ):
                yield member


def _validate(connector_class: type[SourceConnector]) -> tuple[str, str]:
    """Check that a connector class declares the attributes the pipeline keys on.

    Args:
        connector_class: The class to check.

    Returns:
        Its jurisdiction code, upper-cased, and its connector name.

    Raises:
        TypeError: If the class declares no ``jurisdiction_code`` or no ``name``. Both are
            written to the database — ``ingest_run.connector`` and the ``ingest_checkpoint``
            primary key — so neither can be left to a default.
    """
    code = getattr(connector_class, "jurisdiction_code", None)
    name = getattr(connector_class, "name", None)
    if not isinstance(code, str) or not code.strip():
        message = f"{connector_class.__qualname__} must declare a non-empty jurisdiction_code"
        raise TypeError(message)
    if not isinstance(name, str) or not name.strip():
        message = f"{connector_class.__qualname__} must declare a non-empty name"
        raise TypeError(message)
    return code.strip().upper(), name.strip()


def register_connector(connector_class: type[SourceConnector]) -> type[SourceConnector]:
    """Register a connector class explicitly.

    Discovery covers the shipped connectors; this exists for tests, which register a fake,
    and for a connector that lives outside the package.

    Args:
        connector_class: The class to register.

    Returns:
        The class, so this can be used as a decorator.

    Raises:
        TypeError: If the class does not declare a jurisdiction code and a name.
        ValueError: If another class already serves that jurisdiction.
    """
    code, name = _validate(connector_class)
    registry = _ensure_registry()
    existing = registry.get(code)
    if existing is not None and existing is not connector_class:
        message = (
            f"two connectors claim jurisdiction {code}: {existing.__qualname__} and "
            f"{connector_class.__qualname__}. A jurisdiction with two sources needs the "
            "runner to select on connector name, which is a deliberate change to make."
        )
        raise ValueError(message)
    registry[code] = connector_class
    log.debug("connector registered", extra={"context": {"jurisdiction": code, "connector": name}})
    return connector_class


def _ensure_registry() -> dict[str, type[SourceConnector]]:
    """Return the registry, running discovery the first time.

    Returns:
        The mutable registry mapping.
    """
    global _registry  # module-level cache; reset_registry() clears it for tests
    if _registry is None:
        _registry = {}
        for connector_class in _iter_candidate_classes():
            register_connector(connector_class)
    return _registry


def reset_registry(*connector_classes: type[SourceConnector]) -> None:
    """Forget every registration, so the next lookup rediscovers.

    Used by tests, which must not leak a fake connector into another test's run.

    Args:
        *connector_classes: Classes to seed the registry with instead of rediscovering.
            Passing any of them makes this the *whole* registry, which is what a test driving
            the pipeline through a fake needs: once a jurisdiction ships a real connector,
            discovery would otherwise claim that jurisdiction first and the fake would be
            refused as a second claimant. Passing none restores ordinary discovery.
    """
    global _registry  # module-level cache
    _registry = None
    if not connector_classes:
        return
    _registry = {}
    for connector_class in connector_classes:
        register_connector(connector_class)


def connector_classes() -> Mapping[str, type[SourceConnector]]:
    """Return every known connector class, keyed by jurisdiction code.

    Returns:
        A read-only view of the registry.
    """
    return dict(_ensure_registry())


def available_jurisdictions() -> tuple[str, ...]:
    """Return the jurisdiction codes a connector exists for, in a stable order.

    Returns:
        The codes, sorted, which is what ``plt ingest --all`` iterates over.
    """
    return tuple(sorted(_ensure_registry()))


def connector_for(jurisdiction_code: str, settings: Settings | None = None) -> SourceConnector:
    """Build the connector serving a jurisdiction.

    Args:
        jurisdiction_code: Jurisdiction code such as ``NL`` or ``EU``, case-insensitive.
        settings: Settings passed to the connector. Defaults to the process-wide settings.

    Returns:
        A newly constructed connector.

    Raises:
        ConnectorNotFoundError: If no connector serves that jurisdiction.
    """
    code = jurisdiction_code.strip().upper()
    connector_class = _ensure_registry().get(code)
    if connector_class is None:
        known = ", ".join(available_jurisdictions()) or "none"
        message = (
            f"no connector for jurisdiction {code!r}; a jurisdiction is onboarded by adding "
            f"a connector under plt.pipeline.connectors and a keyword list under "
            f"data/keywords/. Known jurisdictions: {known}"
        )
        raise ConnectorNotFoundError(message)
    return connector_class(settings)
