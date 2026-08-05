"""Per-connector checkpoint read and write.

Backed by the ``ingest_checkpoint`` table (``docs/architecture.md`` section 3): one row per
connector, holding the highest source modification timestamp processed, the last source
cursor and the last identifier seen. The weekly job runs with no explicit window and lets
this row supply it (section 7), so the checkpoint is what makes ingestion incremental rather
than a full re-crawl every week.

Two rules govern writes, and both exist because the alternative loses data:

* **A run that failed does not advance the checkpoint.** The window is retried in full next
  time; deduplication absorbs the overlap.
* **The checkpoint never moves backwards.** :meth:`Checkpoint.advanced_to` keeps the maximum
  of the timestamps offered to it, so a source that yields slightly out of order — or a
  connector that yields a corrected document with an older timestamp — cannot rewind the
  position and cause the next run to re-crawl a year of case law.

The window a run is given is inclusive at the lower bound: a document modified in the same
second as the checkpoint is offered again and skipped by deduplication, which is much
cheaper than the alternative failure mode of never seeing it at all.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from plt.db.models import IngestCheckpoint
from plt.utils.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = [
    "Checkpoint",
    "clear_checkpoint",
    "read_checkpoint",
    "resolve_since",
    "write_checkpoint",
]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """The resumable position of one connector.

    A value object: reading returns one, advancing returns a new one, writing persists one.
    The runner therefore accumulates a *pending* position in memory and only writes it once
    the batch that produced it has been committed, which is what keeps the stored position
    from claiming work that was rolled back.

    Attributes:
        connector: Connector name; the primary key of ``ingest_checkpoint``.
        jurisdiction_code: Jurisdiction the connector serves.
        last_modified_seen: Highest source modification timestamp successfully processed.
            The next run starts here.
        last_cursor: Opaque source cursor for resuming mid-window.
        last_source_id: Identifier of the last document processed, for sources with no
            cursor of their own.
        updated_at: When the row was last written; ``None`` for a position that has not been
            persisted yet.
    """

    connector: str
    jurisdiction_code: str
    last_modified_seen: datetime | None = None
    last_cursor: str | None = None
    last_source_id: str | None = None
    updated_at: datetime | None = None

    @property
    def is_empty(self) -> bool:
        """Return whether the position holds nothing to resume from."""
        return (
            self.last_modified_seen is None
            and self.last_cursor is None
            and self.last_source_id is None
        )

    def advanced_to(
        self,
        *,
        modified_at: datetime | None = None,
        cursor: str | None = None,
        source_id: str | None = None,
    ) -> Checkpoint:
        """Return the position advanced over one processed document.

        Args:
            modified_at: Source modification timestamp of the document, if it has one.
            cursor: Source cursor the document was found at, if the source exposes one.
            source_id: Identifier of the document.

        Returns:
            A new checkpoint. The timestamp only ever moves forward; the cursor and
            identifier follow the most recent document, since both describe a position in
            the stream rather than a high-water mark.
        """
        highest = self.last_modified_seen
        if modified_at is not None and (highest is None or modified_at > highest):
            highest = modified_at
        return replace(
            self,
            last_modified_seen=highest,
            last_cursor=cursor if cursor is not None else self.last_cursor,
            last_source_id=source_id if source_id is not None else self.last_source_id,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form stored on ``ingest_run``.

        Returns:
            The position as plain types, with timestamps in ISO 8601, so that
            ``checkpoint_before`` and ``checkpoint_after`` make a run replayable by hand.
        """
        return {
            "connector": self.connector,
            "jurisdiction_code": self.jurisdiction_code,
            "last_modified_seen": (
                self.last_modified_seen.isoformat() if self.last_modified_seen else None
            ),
            "last_cursor": self.last_cursor,
            "last_source_id": self.last_source_id,
        }


def read_checkpoint(session: Session, connector: str) -> Checkpoint | None:
    """Read the stored position of a connector.

    Args:
        session: Open database session.
        connector: Connector name, e.g. ``rechtspraak``.

    Returns:
        The stored position, or ``None`` when the connector has never completed a run.
    """
    row = session.scalars(
        select(IngestCheckpoint).where(IngestCheckpoint.connector == connector)
    ).one_or_none()
    if row is None:
        return None
    return Checkpoint(
        connector=row.connector,
        jurisdiction_code=row.jurisdiction_code,
        last_modified_seen=row.last_modified_seen,
        last_cursor=row.last_cursor,
        last_source_id=row.last_source_id,
        updated_at=row.updated_at,
    )


def write_checkpoint(session: Session, checkpoint: Checkpoint) -> None:
    """Store a connector's position, inserting the row on first use.

    Nothing is committed here: the caller's transaction decides, so a checkpoint only
    becomes durable together with the work it describes.

    Args:
        session: Open database session.
        checkpoint: The position to store.
    """
    row = session.get(IngestCheckpoint, checkpoint.connector)
    if row is None:
        row = IngestCheckpoint(
            connector=checkpoint.connector,
            jurisdiction_code=checkpoint.jurisdiction_code,
        )
        session.add(row)
    row.jurisdiction_code = checkpoint.jurisdiction_code
    row.last_modified_seen = checkpoint.last_modified_seen
    row.last_cursor = checkpoint.last_cursor
    row.last_source_id = checkpoint.last_source_id
    log.debug("checkpoint written", extra={"context": checkpoint.as_dict()})


def clear_checkpoint(session: Session, connector: str) -> bool:
    """Delete a connector's position, so its next run starts from scratch.

    Args:
        session: Open database session.
        connector: Connector name.

    Returns:
        Whether a row was removed.
    """
    row = session.get(IngestCheckpoint, connector)
    if row is None:
        return False
    session.delete(row)
    return True


def resolve_since(explicit: datetime | None, checkpoint: Checkpoint | None) -> datetime | None:
    """Decide the lower bound of the window a run should cover.

    Args:
        explicit: Lower bound the caller asked for, if any. An explicit window always wins,
            so an operator can re-crawl a period without deleting the checkpoint.
        checkpoint: The connector's stored position.

    Returns:
        The lower bound to pass to ``discover``, or ``None`` for no lower bound — which is
        what a first run over a source does.
    """
    if explicit is not None:
        return explicit
    if checkpoint is None:
        return None
    return checkpoint.last_modified_seen
