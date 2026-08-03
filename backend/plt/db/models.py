"""SQLAlchemy ORM models.

Scaffold placeholder. The table set is fixed by the contract in ``docs/architecture.md``
section 3 (``jurisdiction``, ``court``, ``case``, ``case_document``, ``party``, ``topic``,
``case_topic``, ``keyword_match``, ``citation``, ``ingest_run``, ``ingest_checkpoint``) and
is implemented by the database schema issue, not by the scaffold.

Models inherit from :class:`plt.db.base.Base`, use SQLAlchemy 2.0 typed declarative mapping
and store timezone-aware UTC timestamps. ``case`` carries
``UNIQUE (jurisdiction_code, source_id)``, which is the deduplication key.
"""

from __future__ import annotations

from plt.db.base import Base

__all__: list[str] = []

# Alembic imports this module so that every model is registered on ``Base.metadata``
# before autogenerate compares it against the database.
_ = Base
