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
  plain migration on both back ends. :func:`include_object` is the Alembic autogenerate
  filter that goes with it, and says why.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, TypeVar

from sqlalchemy import DateTime, Enum, MetaData, TypeDecorator
from sqlalchemy.orm import DeclarativeBase

if TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import Dialect
    from sqlalchemy.sql.schema import SchemaItem

__all__ = [
    "NAMING_CONVENTION",
    "Base",
    "UtcDateTime",
    "include_object",
    "metadata",
    "portable_enum",
    "portable_enum_constraint_names",
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


def portable_enum_constraint_names() -> frozenset[str]:
    """Return the name of every CHECK constraint :func:`portable_enum` generates.

    The names are derived from the metadata rather than listed literally, so a column added
    with :func:`portable_enum` is covered without anyone remembering to update this module.
    Requires :mod:`plt.db.models` to have been imported, which is what populates the tables.

    Returns:
        The constraint names, rendered through :data:`NAMING_CONVENTION`.
    """
    names: set[str] = set()
    for table in metadata.tables.values():
        for column in table.columns:
            column_type = column.type
            if (
                isinstance(column_type, Enum)
                and not column_type.native_enum
                and column_type.create_constraint
                and column_type.name is not None
            ):
                names.add(
                    NAMING_CONVENTION["ck"]
                    % {"table_name": table.name, "constraint_name": column_type.name}
                )
    return frozenset(names)


def include_object(
    object_: SchemaItem,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: SchemaItem | None,
) -> bool:
    """Filter the schema objects Alembic autogenerate is allowed to compare.

    Alembic 1.19 began comparing CHECK constraints, and it cannot compare the ones
    :func:`portable_enum` produces. SQLAlchemy marks a constraint generated from a column
    *type* as type-bound, and alembic deliberately leaves those out of the model side of the
    comparison (``autogenerate/compare/check_constraints.py``) while reflecting them from the
    database like any other named constraint. The constraint is therefore present on one side
    only, and every ``portable_enum`` column is reported as a removal on every run — against a
    database created by ``metadata.create_all()`` from that very metadata just the same, which
    is what proves the report is an artefact rather than drift.

    Acting on those reports would drop the constraints that section 3 of
    ``docs/architecture.md`` requires, so they are excluded here instead: from the drift test,
    and from ``alembic revision --autogenerate``, which would otherwise write six spurious
    ``drop_constraint`` calls into the next revision anyone generates. Hand-written CHECK
    constraints are *not* type-bound, compare correctly, and stay in the comparison. That the
    values still agree is covered directly by
    ``tests/unit/test_migrations.py::test_the_migrated_check_constraints_match_the_model_enums``.

    Args:
        object_: The schema object alembic is considering. Unused; the name identifies it.
        name: The object's name, or ``None`` for an unnamed object.
        type_: The kind of object, e.g. ``"table"`` or ``"check_constraint"``.
        reflected: Whether the object came from the database rather than the models. Unused;
            the constraint is excluded on both sides.
        compare_to: The object on the other side of the comparison, or ``None``. Unused.

    Returns:
        ``False`` for a ``portable_enum`` CHECK constraint, ``True`` for everything else.
    """
    del object_, reflected, compare_to
    return not (type_ == "check_constraint" and name in portable_enum_constraint_names())


class Base(DeclarativeBase):
    """Declarative base for every PLT ORM model.

    Uses SQLAlchemy 2.0 typed declarative mapping: models annotate columns with
    ``Mapped[...]`` / ``mapped_column(...)`` so that mypy checks the model layer.
    """

    metadata = metadata
