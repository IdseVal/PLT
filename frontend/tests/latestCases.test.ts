/**
 * `getLatestCases` and the date helper the sidebar renders through.
 *
 * The endpoint's own bounds (`limit` at most 50, `docs/architecture.md` section 5) are
 * enforced client-side as well, so a caller cannot send a request the API will reject, and
 * the payload is narrowed rather than cast, so a contract mismatch surfaces as an error
 * instead of as a crash inside a component.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, LATEST_CASES_MAX_LIMIT, getLatestCases } from '@/api/client'
import { formatDecisionDate } from '@/utils/dates'

/**
 * Stub `fetch` with a fixed JSON body.
 *
 * @param body - Response body.
 * @returns The mock, for asserting on the request URL.
 */
function stubJson(body: unknown): ReturnType<typeof vi.fn> {
  // A fresh Response per call: a body can only be read once, and some tests fetch twice.
  const fetchMock = vi.fn().mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** A well-formed element of the feed. */
const VALID = {
  jurisdiction_code: 'NL',
  jurisdiction_name: 'Netherlands',
  source_id: 'ECLI:NL:HR:2024:1',
  title: 'A case',
  court_name: 'Hoge Raad',
  decision_date: '2024-03-12',
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('getLatestCases', () => {
  it('requests the documented endpoint with the limit', async () => {
    const fetchMock = stubJson({ items: [VALID] })

    await getLatestCases(20)

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe('/api/cases/latest?limit=20')
  })

  it('clamps the limit to the range the endpoint accepts', async () => {
    const fetchMock = stubJson({ items: [] })

    await getLatestCases(500)
    await getLatestCases(0)
    await getLatestCases(20.7)

    const limits = fetchMock.mock.calls.map((call) => String(call[0]).split('limit=')[1])
    expect(limits).toEqual([String(LATEST_CASES_MAX_LIMIT), '1', '20'])
  })

  it('fills in the fields a source may omit', async () => {
    stubJson({ items: [{ jurisdiction_code: 'EU', source_id: '62021CJ0616', title: 'X' }] })

    const cases = await getLatestCases()

    expect(cases[0]).toEqual({
      jurisdiction_code: 'EU',
      jurisdiction_name: null,
      source_id: '62021CJ0616',
      title: 'X',
      court_name: null,
      decision_date: null,
    })
  })

  it('drops an element that cannot identify a case, keeping the rest', async () => {
    stubJson({ items: [VALID, { title: 'no identifiers' }, null] })

    const cases = await getLatestCases()

    expect(cases).toHaveLength(1)
    expect(cases[0]?.source_id).toBe(VALID.source_id)
  })

  it('reports a body that is neither an array nor a paginated envelope', async () => {
    stubJson({ unexpected: true })

    const error: unknown = await getLatestCases().catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('invalid_response')
  })

  it('reports a non-empty list in which nothing is a case', async () => {
    stubJson([{ nonsense: true }])

    await expect(getLatestCases()).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('passes an abort signal through to the request', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('aborted', 'AbortError')))
    const controller = new AbortController()
    controller.abort()

    await expect(getLatestCases(20, controller.signal)).rejects.toMatchObject({
      code: 'request_aborted',
    })
  })
})

describe('formatDecisionDate', () => {
  it.each([
    ['2024-03-12', '12 March 2024'],
    ['2024-03-12T00:00:00Z', '12 March 2024'],
    // A timestamp late in the UTC day keeps its own calendar date, whatever the reader's zone.
    ['2024-03-12T23:30:00Z', '12 March 2024'],
  ])('formats %s as %s', (value, expected) => {
    expect(formatDecisionDate(value)).toBe(expected)
  })

  it.each([[null], [''], ['   '], ['not a date']])('returns null for %s', (value) => {
    expect(formatDecisionDate(value)).toBeNull()
  })
})
