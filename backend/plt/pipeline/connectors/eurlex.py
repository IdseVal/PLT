"""European Union connector: EUR-Lex / CELLAR.

Scaffold placeholder. Implemented by the EU connector issue against
``settings.eurlex_sparql_url`` (CELLAR SPARQL over the CDM ontology, enumerating CELEX
sector 6 by document date) and ``settings.eurlex_cellar_base_url`` (CELLAR REST notices and
language manifestations).

A single CELLAR search returns at most 10,000 results, so discovery pages by date window
(``docs/core-document.md`` annex 2a).
"""

from __future__ import annotations

__all__: list[str] = []
