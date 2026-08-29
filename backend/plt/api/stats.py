"""Aggregate endpoints: ``/api/stats/jurisdictions`` and ``/api/filters``.

Both answer a whole UI surface in one request.

``/api/stats/jurisdictions`` is the map payload. It resolves in a single aggregate query
covering every jurisdiction, the EU included, and it keeps the jurisdictions whose case
count is zero: the map renders intended coverage in a muted state rather than dropping the
shape, so a reader can see that a jurisdiction is tracked but has no data yet
(``docs/architecture.md`` sections 5 and 6). ``map_feature_id`` is what the frontend
resolves a shape against - the ISO 3166-1 alpha-2 code for a state, the sentinel ``EU`` for
the Union, which the map draws as the hoverable North Sea logo.

``/api/filters`` sits at the API root rather than under ``/api/stats``, which is why it has
a blueprint of its own; section 1 fixes the file list of this package, so the two live in
one module. ``/api/exclusions`` joins it there: it answers the methodology page rather than
a filter control, but it is read from the same curated lists and shares their blueprint.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from flask import Blueprint

from plt.api.schemas import exclusions_payload, facets_payload, jurisdiction_stat_payload
from plt.db.repositories import jurisdiction_stats, list_facets
from plt.extensions import current_settings, db_session

__all__ = ["filters_bp", "stats_bp"]

#: Mounted at ``<api_prefix>/stats`` by :func:`plt.api.register_blueprints`.
stats_bp = Blueprint("stats", __name__)

#: Mounted at ``<api_prefix>`` by :func:`plt.api.register_blueprints`.
filters_bp = Blueprint("filters", __name__)


@stats_bp.get("/jurisdictions")
def jurisdictions() -> tuple[list[dict[str, Any]], int]:
    """Return the case count and latest decision date of every jurisdiction.

    Returns:
        A ``(payload, status)`` pair. The payload is the array section 5 specifies, ordered
        by jurisdiction code and including jurisdictions with no cases.
    """
    stats = jurisdiction_stats(db_session())
    return [jurisdiction_stat_payload(stat) for stat in stats], HTTPStatus.OK


@filters_bp.get("/filters")
def filters() -> tuple[dict[str, Any], int]:
    """Return the facet values behind the All-cases filter UI.

    Returns:
        A ``(payload, status)`` pair listing only values that occur on a published case, so
        the UI cannot offer a filter that yields nothing.
    """
    facets = list_facets(db_session())
    return facets_payload(facets, current_settings()), HTTPStatus.OK


@filters_bp.get("/exclusions")
def exclusions() -> tuple[dict[str, Any], int]:
    """Return what each jurisdiction's criterion deliberately keeps out.

    The methodology page publishes this beside the inclusion criterion, because an inclusion
    criterion on its own is half a method: a reader can see which terms admit a case but not
    which were considered and rejected, which are unable to admit one alone, or which phrases
    veto a document outright.

    It is read from the curated lists and their record of rejected terms, not from the
    database, so it describes the criterion as it stands rather than as some past run applied
    it.

    Returns:
        A ``(payload, status)`` pair, one entry per jurisdiction with published cases.
    """
    facets = list_facets(db_session())
    return exclusions_payload(facets, current_settings()), HTTPStatus.OK
