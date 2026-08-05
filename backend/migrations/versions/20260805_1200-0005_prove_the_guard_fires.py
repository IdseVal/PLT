"""Throwaway revision that collides with 0005 on purpose, to prove CI rejects it (#79).

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-05 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Do nothing; the collision is the point."""


def downgrade() -> None:
    """Do nothing; the collision is the point."""
