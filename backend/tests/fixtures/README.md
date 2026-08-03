# Test fixtures

Recorded source payloads used by the unit tests, so no test touches the network.

One subdirectory per connector (`rechtspraak/`, `eurlex/`), each holding responses captured
verbatim from the live endpoint, named after what they represent (`search-page-1.atom`,
`ECLI_NL_RBDHA_2024_1234.xml`). Record a fixture once, commit it, and mock the HTTP client
against it — never re-fetch during a test run.

Strip nothing from a payload except credentials: the point of a fixture is that it is what
the endpoint actually returned.
