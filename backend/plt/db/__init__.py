"""Persistence layer: declarative base, ORM models, session lifecycle and repositories.

Importing this package registers every model on :data:`plt.db.base.metadata`, which is what
Alembic autogenerate and ``metadata.create_all`` compare against.
"""

from __future__ import annotations

from plt.db.base import Base, UtcDateTime, metadata, utcnow
from plt.db.models import (
    Case,
    CaseDocument,
    CaseTopic,
    Citation,
    Court,
    DocumentType,
    IngestCheckpoint,
    IngestRun,
    IngestStatus,
    Jurisdiction,
    JurisdictionType,
    KeywordMatch,
    LawDomain,
    Party,
    PartyRole,
    Topic,
    TopicAssignmentSource,
)
from plt.db.session import get_engine, get_session_factory, make_engine, session_scope

__all__ = [
    "Base",
    "Case",
    "CaseDocument",
    "CaseTopic",
    "Citation",
    "Court",
    "DocumentType",
    "IngestCheckpoint",
    "IngestRun",
    "IngestStatus",
    "Jurisdiction",
    "JurisdictionType",
    "KeywordMatch",
    "LawDomain",
    "Party",
    "PartyRole",
    "Topic",
    "TopicAssignmentSource",
    "UtcDateTime",
    "get_engine",
    "get_session_factory",
    "make_engine",
    "metadata",
    "session_scope",
    "utcnow",
]
