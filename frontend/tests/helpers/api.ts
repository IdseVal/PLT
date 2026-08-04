/**
 * A stand-in for the PLT API.
 *
 * The API (#10) is built in parallel with these pages, so the pages are tested against the
 * contract in `docs/architecture.md` section 5 rather than against a running server. Unit
 * tests never touch the network (`CONTRIBUTING.md` section 4): `fetch` is replaced with a
 * router over recorded payloads, and every request the page makes is captured so a test can
 * assert on the query string the page actually sent.
 */

import { vi } from 'vitest'

import type { CaseRecord, CaseSummary, FilterFacets, Paginated } from '@/types/api'

/** What a stubbed endpoint returns. */
export interface MockResponse {
  /** HTTP status. Defaults to 200. */
  readonly status?: number
  /** JSON body. */
  readonly body?: unknown
}

/** An endpoint stub: a fixed response, or one computed from the request URL. */
export type MockRoute = MockResponse | ((url: URL) => MockResponse)

/** What {@link mockApi} hands back to the test. */
export interface MockApi {
  /** Every URL requested, in order. */
  readonly urls: URL[]
  /** The most recent request to a path, or `undefined`. */
  readonly lastRequestTo: (pathname: string) => URL | undefined
}

/** Origin relative request URLs are resolved against inside the test environment. */
const TEST_ORIGIN = 'http://localhost'

/**
 * Replace `fetch` with a router over the given endpoints.
 *
 * @param routes - Response per API path, e.g. `{ '/api/cases': { body } }`. A path with no
 *   entry answers 404 in the API's error envelope, which is what an unknown case looks like.
 * @returns A handle on the requests that were made.
 */
export function mockApi(routes: Readonly<Record<string, MockRoute>>): MockApi {
  const urls: URL[] = []

  const fetchMock = vi.fn((input: RequestInfo | URL): Promise<Response> => {
    // The client always calls `fetch` with a URL string; a `Request` would carry its target
    // on `.url`, so both are handled rather than stringified blindly.
    const target = typeof input === 'string' || input instanceof URL ? String(input) : input.url
    const url = new URL(target, TEST_ORIGIN)
    urls.push(url)

    const route = routes[url.pathname]
    if (route === undefined) {
      return Promise.resolve(
        jsonResponse({ error: { code: 'not_found', message: 'No such case.', details: {} } }, 404),
      )
    }

    const result = typeof route === 'function' ? route(url) : route
    return Promise.resolve(jsonResponse(result.body ?? {}, result.status ?? 200))
  })

  vi.stubGlobal('fetch', fetchMock)

  return {
    urls,
    lastRequestTo: (pathname) => [...urls].reverse().find((url) => url.pathname === pathname),
  }
}

/**
 * Build a JSON response.
 *
 * @param body - Payload.
 * @param status - HTTP status.
 * @returns The response.
 */
export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * A case as it appears in a listing.
 *
 * @param overrides - Fields to change.
 * @returns The summary.
 */
export function caseSummary(overrides: Partial<CaseSummary> = {}): CaseSummary {
  return {
    id: 1,
    jurisdiction_code: 'NL',
    jurisdiction_name: 'Netherlands',
    source_id: 'ECLI:NL:HR:2024:1',
    source_system: 'rechtspraak',
    title: 'Stichting Bollenboos v. State of the Netherlands',
    abstract: 'Spraying near residential property; precautionary principle.',
    decision_date: '2024-03-05',
    language: 'nl',
    law_domain: 'public',
    law_subfield: 'administrative',
    court_name: 'Hoge Raad',
    topics: [{ slug: 'spray-zones', label: 'Spray zones' }],
    source_url: 'https://uitspraken.rechtspraak.nl/details?id=ECLI:NL:HR:2024:1',
    ...overrides,
  }
}

/**
 * A full case record.
 *
 * @param overrides - Fields to change.
 * @returns The record.
 */
export function caseRecord(overrides: Partial<CaseRecord> = {}): CaseRecord {
  return {
    ...caseSummary(),
    filing_date: '2022-11-02',
    publication_date: '2024-03-07',
    case_numbers: ['21/04421'],
    procedure_type: 'cassation',
    outcome: 'dismissed',
    parties: [
      { name: 'Stichting Bollenboos', role: 'applicant' },
      { name: 'State of the Netherlands', role: 'defendant' },
    ],
    documents: [
      {
        id: 11,
        language: 'nl',
        doc_type: 'judgment',
        format: 'xml',
        full_text: 'Eerste overweging.\n\nTweede overweging.',
        source_url: 'https://uitspraken.rechtspraak.nl/details?id=ECLI:NL:HR:2024:1',
      },
    ],
    ...overrides,
  }
}

/**
 * A page of search results.
 *
 * @param items - The cases on this page.
 * @param overrides - Paging fields to change.
 * @returns The paginated payload.
 */
export function casePage(
  items: CaseSummary[],
  overrides: Partial<Omit<Paginated<CaseSummary>, 'items'>> = {},
): Paginated<CaseSummary> {
  return {
    items,
    page: 1,
    page_size: 20,
    total: items.length,
    ...overrides,
  }
}

/**
 * The facet payload behind the filter panel.
 *
 * @param overrides - Facets to change.
 * @returns The payload.
 */
export function filterFacets(overrides: Partial<FilterFacets> = {}): FilterFacets {
  return {
    jurisdictions: [
      { code: 'EU', name: 'European Union' },
      { code: 'NL', name: 'Netherlands' },
    ],
    courts: [
      { id: 7, name: 'Hoge Raad' },
      { id: 9, name: 'Court of Justice' },
    ],
    law_domains: ['public', 'private', 'criminal'],
    law_subfields: ['administrative', 'environmental'],
    languages: ['nl', 'en'],
    topics: [
      { slug: 'spray-zones', label: 'Spray zones' },
      { slug: 'authorisation', label: 'Authorisation' },
    ],
    earliest_decision_date: '2001-01-15',
    latest_decision_date: '2026-06-30',
    ...overrides,
  }
}
