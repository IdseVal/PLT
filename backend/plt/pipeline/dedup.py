"""Deduplication on source identifier and content hash.

Scaffold placeholder. The deduplication key is ``UNIQUE (jurisdiction_code, source_id)``
(ECLI or CELEX). On conflict the content hash decides: identical touches ``last_seen_at``
only, different updates the row in place. A second row for the same source identifier is
never inserted (``docs/architecture.md`` section 3).

The pre-check runs before ``fetch`` so unchanged documents are never re-downloaded.
"""

from __future__ import annotations

__all__: list[str] = []
