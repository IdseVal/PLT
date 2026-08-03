"""Netherlands connector: Rechtspraak.nl Open Data.

Scaffold placeholder. Implemented by the NL connector issue against
``settings.rechtspraak_search_url`` (Atom search, paged with ``max``/``from``, filtered on
``modified``) and ``settings.rechtspraak_content_url`` (Rechtspraak XML with a Dublin Core
metadata block, ``inhoudsindicatie`` abstract and ``uitspraak`` full text). Controlled
vocabularies come from ``settings.rechtspraak_vocabulary_url`` rather than being hard-coded.

The endpoint offers no full-text search, so topical selection happens client-side through
the keyword filter (``docs/core-document.md`` annex 2a).
"""

from __future__ import annotations

__all__: list[str] = []
