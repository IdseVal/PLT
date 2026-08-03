"""Pipeline orchestrator.

Scaffold placeholder. The pipeline issue implements::

    run_jurisdiction(code, since=None, until=None, dry_run=False) -> IngestReport

which drives the stage order in ``docs/architecture.md`` section 4, handles ``SIGINT`` by
finishing the item in flight and writing its checkpoint, commits per batch, and under
``dry_run`` writes a match report without touching the database. A failed run must not
advance the checkpoint (section 7).
"""

from __future__ import annotations

__all__: list[str] = []
