"""Declarative base and the database-wide naming convention.

Every ORM model in :mod:`plt.db.models` inherits from :class:`Base`. The naming convention
is set here rather than per-table so that Alembic autogenerates stable, explicit constraint
names, which is what makes a constraint droppable in a later migration on PostgreSQL.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

__all__ = ["NAMING_CONVENTION", "Base", "metadata"]

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


class Base(DeclarativeBase):
    """Declarative base for every PLT ORM model.

    Uses SQLAlchemy 2.0 typed declarative mapping: models annotate columns with
    ``Mapped[...]`` / ``mapped_column(...)`` so that mypy checks the model layer.
    """

    metadata = metadata
