"""Per-connector checkpoint read and write.

Scaffold placeholder. Backed by the ``ingest_checkpoint`` table (``docs/architecture.md``
section 3): one row per connector holding the last modification timestamp seen and the last
cursor, so a re-run after an interruption resumes rather than restarting.
"""

from __future__ import annotations

__all__: list[str] = []
