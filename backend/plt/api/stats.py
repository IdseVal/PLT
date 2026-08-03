"""Statistics endpoints: ``/api/stats`` and below.

Scaffold placeholder. The map payload ``/api/stats/jurisdictions`` must resolve in a single
aggregate query covering every jurisdiction, the EU included, per ``docs/architecture.md``
section 5. Implemented by the API issue.
"""

from __future__ import annotations

from flask import Blueprint

__all__ = ["stats_bp"]

#: Mounted at ``<api_prefix>/stats`` by :func:`plt.api.register_blueprints`.
stats_bp = Blueprint("stats", __name__)
