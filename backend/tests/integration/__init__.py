"""Integration tests.

Tests that hit live external endpoints are marked ``@pytest.mark.integration`` and are
opt-in: ``pytest -m integration``. The default run excludes nothing but contains no live
calls, per ``docs/architecture.md`` section 2.8.
"""

from __future__ import annotations
