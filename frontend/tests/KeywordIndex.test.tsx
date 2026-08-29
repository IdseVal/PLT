/**
 * Keyword index: the per-jurisdiction term listing on the methodology page.
 *
 * No test here touches the network. `fetch` is stubbed per test so that the real
 * `src/api/client.ts` code path runs — the component's contract with the client is part of
 * what is being tested, and mocking the client module would hide it.
 *
 * jsdom implements no disclosure behaviour for `<details>`, so tests that read inside one
 * open it directly rather than clicking the summary; what is asserted is the grouping and
 * the content, not the browser's own toggle.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import KeywordIndex from '@/components/KeywordIndex'
import type { FilterFacets } from '@/types/api'

/** A facet payload with two jurisdictions and terms in more than one category. */
const FACETS = {
  jurisdictions: [
    { code: 'NL', name: 'Netherlands' },
    { code: 'EU', name: 'European Union' },
  ],
  courts: [],
  topics: [],
  law_domains: [],
  law_subfields: [],
  languages: [],
  keywords: [
    // Deliberately out of alphabetical order, so the sorting is what the test exercises.
    {
      id: 'nl-glyfosaat',
      term: 'glyfosaat',
      category: 'active_substance',
      jurisdiction: 'NL',
      case_count: 120,
    },
    { id: 'nl-ctgb', term: 'Ctgb', category: 'authority', jurisdiction: 'NL', case_count: 44 },
    {
      id: 'nl-azijnzuur',
      term: 'azijnzuur',
      category: 'active_substance',
      jurisdiction: 'NL',
      case_count: 1,
    },
    {
      id: 'eu-glyphosate',
      term: 'glyphosate',
      category: 'active_substance',
      jurisdiction: 'EU',
      case_count: 80,
    },
  ],
} satisfies FilterFacets

/**
 * Stub `fetch` with a fixed JSON body.
 *
 * @param body - Response body to return.
 * @param status - HTTP status. Defaults to 200.
 * @returns The mock, for asserting on the request.
 */
function stubJson(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  // A fresh Response per call: a body can only be read once, and a retry fetches again.
  const fetchMock = vi.fn().mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
    ),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/**
 * The `<details>` element whose summary names the jurisdiction, opened for inspection.
 *
 * @param name - The jurisdiction name shown in the summary.
 * @returns The open disclosure element.
 */
function openDisclosure(name: string): HTMLDetailsElement {
  const details = screen.getByText(name).closest('details')
  if (details === null) throw new Error(`No <details> around "${name}"`)
  details.open = true
  return details
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('keyword index', () => {
  it('requests the filter facets and renders one disclosure per jurisdiction', async () => {
    const fetchMock = stubJson(FACETS)
    render(<KeywordIndex />)

    const nl = await screen.findByText('Netherlands')
    const eu = screen.getByText('European Union')

    expect(fetchMock.mock.calls.map((call) => String(call[0]))).toContain('/api/filters')

    // Each jurisdiction is a native disclosure, so it is keyboard-operable for free.
    expect(nl.closest('details')).not.toBeNull()
    expect(eu.closest('details')).not.toBeNull()

    // Jurisdictions keep the payload's own order.
    expect(nl.compareDocumentPosition(eu) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('counts the terms of each list in the summary', async () => {
    stubJson(FACETS)
    render(<KeywordIndex />)

    const nl = (await screen.findByText('Netherlands')).closest('summary')
    const eu = screen.getByText('European Union').closest('summary')

    expect(nl).toHaveTextContent('3 terms')
    expect(eu).toHaveTextContent('1 term')
  })

  it('groups terms by category, sorts them, and shows the case count of each', async () => {
    stubJson(FACETS)
    render(<KeywordIndex />)
    await screen.findByText('Netherlands')

    const nl = openDisclosure('Netherlands')

    const categories = within(nl).getAllByRole('heading', { level: 3 })
    expect(categories.map((heading) => heading.textContent)).toEqual([
      'Active substance',
      'Authority',
    ])

    // Terms are alphabetical within their category, each with its case count.
    const substanceList = categories[0]?.nextElementSibling as HTMLElement
    const terms = within(substanceList)
      .getAllByRole('listitem')
      .map((item) => item.textContent)
    expect(terms).toEqual(['azijnzuur (1 case)', 'glyfosaat (120 cases)'])

    expect(within(nl).getByText('Ctgb')).toBeInTheDocument()

    // The EU term files under the EU list, not the Dutch one.
    expect(within(nl).queryByText('glyphosate')).not.toBeInTheDocument()
    expect(within(openDisclosure('European Union')).getByText('glyphosate')).toBeInTheDocument()
  })

  it('announces a loading state before the lists arrive', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => undefined)),
    )
    render(<KeywordIndex />)

    expect(screen.getByRole('status')).toHaveTextContent(/loading the keyword lists/i)
  })

  it('reports a failed request and loads the index when the retry is used', async () => {
    // The first request fails, the retry succeeds.
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify(FACETS), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        ),
      )
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<KeywordIndex />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/could not be loaded/i)
    expect(alert).toHaveTextContent(/could not reach the case database/i)

    await user.click(screen.getByRole('button', { name: /try again/i }))

    expect(await screen.findByText('Netherlands')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('shows the message the API sent when it rejected the request', async () => {
    stubJson({ error: { code: 'http_error', message: 'The filters are unavailable.' } }, 503)
    render(<KeywordIndex />)

    expect(await screen.findByRole('alert')).toHaveTextContent('The filters are unavailable.')
  })

  it('explains a payload without keywords instead of rendering nothing', async () => {
    stubJson({ ...FACETS, keywords: [] })
    render(<KeywordIndex />)

    expect(
      await screen.findByText(/keyword lists are not available right now/i),
    ).toBeInTheDocument()
  })

  it('renders a term containing markup as text', async () => {
    stubJson({
      ...FACETS,
      keywords: [
        {
          id: 'nl-hostile',
          term: '<img src=x onerror="alert(1)">chloorpyrifos',
          category: 'active_substance',
          jurisdiction: 'NL',
          case_count: 2,
        },
      ],
    })
    const { container } = render(<KeywordIndex />)

    await screen.findByText('Netherlands')
    expect(container.querySelector('img')).toBeNull()
  })
})
