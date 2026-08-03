"""Case endpoints: ``/api/cases`` and below.

Scaffold placeholder. The blueprint exists so that registration, URL prefixes and rate
limits are wired from the start; the routes themselves (``/api/cases``,
``/api/cases/latest``, ``/api/cases/<jurisdiction>/<source_id>``, ``/api/cases/export``) are
implemented by the API issue against the contract in ``docs/architecture.md`` section 5.
"""

from __future__ import annotations

from flask import Blueprint

__all__ = ["cases_bp"]

#: Mounted at ``<api_prefix>/cases`` by :func:`plt.api.register_blueprints`.
cases_bp = Blueprint("cases", __name__)
