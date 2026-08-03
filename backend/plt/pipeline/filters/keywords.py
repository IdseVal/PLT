"""Stage 1: the curated keyword matcher.

Scaffold placeholder. The filter issue implements matching over the per-jurisdiction lists
in ``data/keywords/`` - located through :meth:`plt.config.Settings.keyword_list_path`, never
by a hard-coded path - honouring the semantics in ``data/keywords/schema.json``: weights,
per-field multipliers, ``count_term_once``, ``match`` modes, ``aliases``, ``requires`` and
``exclusions``.

Every matched term id is recorded against the case in ``keyword_match`` so the content
manager can review precision and recall (``docs/architecture.md`` section 3).
"""

from __future__ import annotations

__all__: list[str] = []
