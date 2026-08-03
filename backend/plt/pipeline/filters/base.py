"""Filter interface.

Scaffold placeholder. The filter issue implements the ABC from ``docs/architecture.md``
section 4::

    class Filter(ABC):
        def evaluate(self, case: NormalisedCase) -> FilterResult: ...

``FilterResult`` carries ``passed``, ``score``, ``matches`` and a human-readable reason for
the pipeline report.
"""

from __future__ import annotations

__all__: list[str] = []
