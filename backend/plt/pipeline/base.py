"""Connector interface and the data classes that cross pipeline stages.

Scaffold placeholder. The pipeline issue implements the ``SourceConnector`` ABC together
with ``Candidate``, ``RawDocument`` and ``NormalisedCase`` exactly as specified in
``docs/architecture.md`` section 4::

    class SourceConnector(ABC):
        jurisdiction_code: str
        name: str

        def discover(self, since, until) -> Iterator[Candidate]: ...
        def fetch(self, candidate) -> RawDocument: ...
        def normalise(self, raw) -> NormalisedCase: ...

``discover`` streams oldest first, and ``RawDocument`` keeps the source response verbatim so
reclassification never needs a re-fetch (section 2.6).
"""

from __future__ import annotations

__all__: list[str] = []
