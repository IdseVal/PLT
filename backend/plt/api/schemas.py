"""Request and response (de)serialisation and validation.

Scaffold placeholder. The API issue implements the pydantic models that validate and bound
every query parameter server-side (``page``, ``page_size``, ``limit``, dates, sort keys) and
that serialise cases, documents, parties, topics and matched terms for the responses listed
in ``docs/architecture.md`` section 5.

Bounds come from :class:`plt.config.Settings` (``page_size_default``, ``page_size_max``,
``latest_limit_default``, ``latest_limit_max``); they are never literals in a route.
"""

from __future__ import annotations

__all__: list[str] = []
