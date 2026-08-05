"""Per-jurisdiction source connectors.

One module per jurisdiction, each implementing ``SourceConnector``. Endpoints, request rate,
timeouts and retry behaviour come from :class:`plt.config.Settings`; connectors are polite
to what are public research endpoints (``docs/architecture.md`` section 2.5).
"""

from __future__ import annotations
