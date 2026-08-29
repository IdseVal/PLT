"""Integration tests.

Tests that hit live external endpoints are marked ``@pytest.mark.integration`` and are
opt-in: ``pytest -m integration``. The default run excludes nothing but contains no live
calls, per ``docs/architecture.md`` section 2.8.

What belongs here rather than in a unit test is anything about how a source *behaves* as
opposed to what it returns, because a recorded payload cannot hold behaviour and the fake
built from it has to invent some (``docs/architecture.md`` section 2.9). Each connector
therefore carries a **paging-integrity** test — walk one discovery window in pages, and
assert the union of the pages is the window, measured against the count the source states
for itself. Both directions are asserted: a walk that loses one record and repeats another
returns exactly the expected number of rows.
"""

from __future__ import annotations
