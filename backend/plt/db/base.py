"""Declarative base, naming convention and the column types shared by every model.

Every ORM model in :mod:`plt.db.models` inherits from :class:`Base`. The naming convention
is set here rather than per-table so that Alembic autogenerates stable, explicit constraint
names, which is what makes a constraint droppable in a later migration on PostgreSQL.

Two portability helpers live here as well, because both are schema-wide contracts rather
than properties of a single table:

* :class:`UtcDateTime` — every timestamp column in the schema is timezone-aware UTC
  (``docs/architecture.md`` section 3). SQLite has no timestamp-with-zone type, so the
  decorator normalises on the way in and re-attaches :data:`datetime.UTC` on the way out.
  PostgreSQL stores the same column as ``TIMESTAMP WITH TIME ZONE``.
* :func:`portable_enum` — enumerated columns are ``VARCHAR`` plus a ``CHECK`` constraint
  rather than a native PostgreSQL ``ENUM`` type, which keeps the value set alterable in a
  plain migration on both back ends.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, TypeVar

from sqlalchemy import DateTime, Enum, MetaData, TypeDecorator
from sqlalchemy.orm import DeclarativeBase

if TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import Dialect

__all__ = [
    "NAMING_CONVENTION",
    "Base",
    "UtcDateTime",
    "metadata",
    "portable_enum",
    "utcnow",
]

#: Deterministic constraint names shared by SQLite (development) and PostgreSQL.
NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: Shared metadata; Alembic compares migrations against this object.
metadata: Final[MetaData] = MetaData(naming_convention=NAMING_CONVENTION)

#: Any :class:`enum.Enum` subclass, used to type :func:`portable_enum`.
_EnumT = TypeVar("_EnumT", bound=enum.Enum)


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC timestamp.

    Used as the Python-side default of the schema's timestamp columns so that development
    on SQLite and deployment on PostgreSQL produce identical values.

    Returns:
        The current moment with :data:`datetime.UTC` attached.
    """
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """A timestamp column that is always timezone-aware UTC.

    Naive values are rejected rather than silently reinterpreted: a naive timestamp in this
    code base is a bug at the call site, and guessing its zone would corrupt the ingestion
    checkpoints that drive incremental runs.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Normalise a value on its way into the database.

        Args:
            value: The timestamp being persisted, or ``None``.
            dialect: The dialect in use. Unused; the conversion is dialect-independent.

        Returns:
            The value converted to UTC, or ``None``.

        Raises:
            ValueError: If the value is naive, i.e. carries no ``tzinfo``.
        """
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            message = "naive datetime rejected; PLT timestamps are timezone-aware UTC"
            raise ValueError(message)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Re-attach UTC on the way out.

        SQLite returns naive datetimes because it stores the column as text; PostgreSQL
        returns an aware value already. Both paths end up as UTC-aware here.

        Args:
            value: The timestamp read from the database, or ``None``.
            dialect: The dialect in use. Unused; the conversion is dialect-independent.

        Returns:
            A timezone-aware UTC timestamp, or ``None``.
        """
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def portable_enum(enum_type: type[_EnumT], name: str) -> Enum:
    """Build an enumerated column type that is portable across SQLite and PostgreSQL.

    The column is rendered as ``VARCHAR`` with a named ``CHECK`` constraint instead of a
    native PostgreSQL ``ENUM``, so that adding a member later is an ordinary migration on
    both back ends. Values are persisted as the enum's *values* (lower-case strings), not
    its member names, because those values are what the HTTP API exposes.

    Args:
        enum_type: The :class:`enum.Enum` subclass to persist.
        name: Constraint name stem, e.g. ``jurisdiction_type``. Must be unique across the
            schema because the naming convention derives the CHECK constraint name from it.

    Returns:
        A configured :class:`sqlalchemy.Enum` instance.
    """
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [str(member.value) for member in members],
    )


class Base(DeclarativeBase):
    """Declarative base for every PLT ORM model.

    Uses SQLAlchemy 2.0 typed declarative mapping: models annotate columns with
    ``Mapped[...]`` / ``mapped_column(...)`` so that mypy checks the model layer.
    """

    metadata = metadata
