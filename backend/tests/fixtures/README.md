# Test fixtures

Recorded source payloads used by the unit tests, so no test touches the network.

One subdirectory per connector (`rechtspraak/`, `eurlex/`), each holding responses captured
verbatim from the live endpoint, named after what they represent (`search-page-1.atom`,
`ECLI_NL_RBDHA_2024_1234.xml`). Record a fixture once, commit it, and mock the HTTP client
against it — never re-fetch during a test run.

Strip nothing from a payload except credentials: the point of a fixture is that it is what
the endpoint actually returned. `.gitattributes` keeps this directory out of the repository's
line-ending normalisation for the same reason.

## `rechtspraak/`

Captured from `data.rechtspraak.nl` on 4 August 2026.

| File | What it is |
| --- | --- |
| `search-page-1.atom` | Three entries of the Atom search feed, `sort=ASC` over a one-hour `modified` window. |
| `search-page-empty.atom` | The same feed with no hits, which is how paging ends. |
| `ECLI_NL_CBB_2024_147.xml` | A College van Beroep voor het bedrijfsleven judgment: statute reference, a repeated `psi:procedure`, an `inhoudsindicatie` and an `uitspraak` body. The field-by-field assertion runs against this one. |
| `ECLI_NL_HR_2024_309.xml` | A Hoge Raad judgment carrying a `dcterms:relation` to the advocate general's opinion. |
| `ECLI_NL_PHR_2024_321.xml` | An opinion, whose body element is `conclusie` rather than `uitspraak`. |
| `ECLI_NL_GHAMS_2026_1495.xml` | A metadata-only ECLI: no summary, no body, nothing for the filter to read. |
| `instanties.xml` | The `Waardelijst/Instanties` vocabulary, **trimmed** to fourteen courts covering every `Type` it defines. The full list runs to 261 entries and 79 kB. |

Three files here were **not** captured from the endpoint, because no court publishes them:

| File | Why it is hand-built |
| --- | --- |
| `multivalued.xml` | Repeats `psi:zaaknummer`, `dcterms:subject`, `dcterms:references`, `psi:procedure` and the *vindplaatsen* list at once, and is about a pesticide authorisation, so one document exercises every multi-valued path and the shipped Dutch keyword list. |
| `external-entity.xml` | An XXE attempt: a `SYSTEM` entity pointing at a local file and another at a remote host. |
| `entity-expansion.xml` | A billion-laughs bomb. |
