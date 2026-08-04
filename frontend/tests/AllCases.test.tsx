/**
 * The All-cases page.
 *
 * The behaviour that matters here is the round trip: what the reader selects has to end up
 * in the address bar, what is in the address bar has to end up in the controls and in the
 * request, and the download has to carry the same selection. Pagination is checked at its
 * three edges — first page, last page and an empty result set — because those are where a
 * paginator normally breaks.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AllCases from '@/pages/AllCases'
import { casePage, caseSummary, filterFacets, mockApi, type MockApi } from './helpers/api'

/** Path of the search endpoint under the default API base URL. */
const CASES = '/api/cases'

/** Path of the facet endpoint. */
const FILTERS = '/api/filters'

/**
 * Reports the current location, so a test can assert on what the page wrote to the URL.
 *
 * @returns A paragraph holding the query string.
 */
function LocationProbe(): JSX.Element {
  const { search } = useLocation()
  return <p data-testid="location">{search}</p>
}

/**
 * Render the page at a URL.
 *
 * @param search - Query string, including the leading `?`, or an empty string.
 */
function renderPage(search = ''): void {
  render(
    <MemoryRouter initialEntries={[`/cases${search}`]}>
      <AllCases />
      <LocationProbe />
    </MemoryRouter>,
  )
}

/**
 * The query string currently in the address bar.
 *
 * @returns The query string.
 */
function currentSearch(): string {
  return screen.getByTestId('location').textContent ?? ''
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('AllCases', () => {
  /**
   * Stub the two endpoints the page reads.
   *
   * @param page - The result page to return.
   * @returns The request log.
   */
  function stubApi(page = casePage([caseSummary()])): MockApi {
    return mockApi({
      [CASES]: { body: page },
      [FILTERS]: { body: filterFacets() },
    })
  }

  it('lists the collection with a result count and a link into each case', async () => {
    stubApi(casePage([caseSummary()], { total: 1 }))
    renderPage()

    expect(await screen.findByText('1 case in the collection.')).toBeInTheDocument()

    const result = screen.getByRole('link', {
      name: 'Stichting Bollenboos v. State of the Netherlands',
    })
    expect(result).toHaveAttribute('href', '/cases/NL/ECLI%3ANL%3AHR%3A2024%3A1')
  })

  it('links every case to its original publication page', async () => {
    stubApi()
    renderPage()

    const source = await screen.findByRole('link', { name: /Original publication/ })
    expect(source).toHaveAttribute(
      'href',
      'https://uitspraken.rechtspraak.nl/details?id=ECLI:NL:HR:2024:1',
    )
    expect(source).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('restores every filter in the URL into the controls and into the request', async () => {
    const api = stubApi()
    renderPage(
      '?q=glyfosaat&jurisdiction=NL&law_domain=public&law_subfield=administrative' +
        '&topic=spray-zones&court=7&language=nl&date_from=2020-01-01&date_to=2024-12-31' +
        '&sort=date_asc&page=2&page_size=50',
    )

    expect(await screen.findByRole('checkbox', { name: /Netherlands/ })).toBeChecked()
    expect(screen.getByRole('searchbox', { name: 'Search' })).toHaveValue('glyfosaat')
    expect(screen.getByRole('combobox', { name: 'Law domain' })).toHaveValue('public')
    expect(screen.getByRole('combobox', { name: 'Law subfield' })).toHaveValue('administrative')
    expect(screen.getByRole('combobox', { name: 'Topic' })).toHaveValue('spray-zones')
    expect(screen.getByRole('combobox', { name: 'Court' })).toHaveValue('7')
    expect(screen.getByRole('combobox', { name: 'Language' })).toHaveValue('nl')
    expect(screen.getByRole('combobox', { name: 'Sort by' })).toHaveValue('date_asc')

    const request = api.lastRequestTo(CASES)
    expect(request?.searchParams.get('q')).toBe('glyfosaat')
    expect(request?.searchParams.getAll('jurisdiction')).toEqual(['NL'])
    expect(request?.searchParams.get('law_domain')).toBe('public')
    expect(request?.searchParams.get('topic')).toBe('spray-zones')
    expect(request?.searchParams.get('court')).toBe('7')
    expect(request?.searchParams.get('date_from')).toBe('2020-01-01')
    expect(request?.searchParams.get('sort')).toBe('date_asc')
    expect(request?.searchParams.get('page')).toBe('2')
    expect(request?.searchParams.get('page_size')).toBe('50')
  })

  it('composes several filters into one URL and one request', async () => {
    const api = stubApi()
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('checkbox', { name: /Netherlands/ })

    await user.type(screen.getByRole('searchbox', { name: 'Search' }), 'glyfosaat')
    await user.click(screen.getByRole('checkbox', { name: /Netherlands/ }))
    await user.click(screen.getByRole('checkbox', { name: /European Union/ }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Law domain' }), 'public')
    await user.click(screen.getByRole('button', { name: 'Apply filters' }))

    await waitFor(() => {
      expect(currentSearch()).toBe(
        '?q=glyfosaat&jurisdiction=NL&jurisdiction=EU&law_domain=public',
      )
    })

    await waitFor(() => {
      const request = api.lastRequestTo(CASES)
      expect(request?.searchParams.getAll('jurisdiction')).toEqual(['NL', 'EU'])
      expect(request?.searchParams.get('law_domain')).toBe('public')
    })
  })

  it('returns to the first page when the selection changes', async () => {
    stubApi(casePage([caseSummary()], { page: 3, total: 120 }))
    const user = userEvent.setup()
    renderPage('?page=3')

    await screen.findByRole('checkbox', { name: /Netherlands/ })
    await user.selectOptions(screen.getByRole('combobox', { name: 'Sort by' }), 'date_asc')

    await waitFor(() => {
      expect(currentSearch()).toBe('?sort=date_asc')
    })
  })

  it('refuses a date range that ends before it starts, without touching the URL', async () => {
    stubApi()
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('checkbox', { name: /Netherlands/ })
    await user.type(screen.getByLabelText('From'), '2024-12-31')
    await user.type(screen.getByLabelText('To'), '2020-01-01')
    await user.click(screen.getByRole('button', { name: 'Apply filters' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/falls after its end/)
    expect(currentSearch()).toBe('')
  })

  it('clears every filter back to the unfiltered listing', async () => {
    stubApi()
    const user = userEvent.setup()
    renderPage('?q=glyfosaat&jurisdiction=NL')

    await screen.findByRole('checkbox', { name: /Netherlands/ })
    await user.click(screen.getByRole('button', { name: 'Clear all' }))

    await waitFor(() => {
      expect(currentSearch()).toBe('')
    })
  })

  describe('pagination', () => {
    it('offers no previous page on the first page', async () => {
      stubApi(casePage([caseSummary()], { page: 1, page_size: 20, total: 120 }))
      renderPage()

      const pagination = await screen.findByRole('navigation', { name: 'Pagination' })
      expect(within(pagination).queryByRole('link', { name: 'Previous' })).not.toBeInTheDocument()
      expect(within(pagination).getByRole('link', { name: 'Next' })).toHaveAttribute(
        'href',
        '/cases?page=2',
      )
      expect(within(pagination).getByText('Page 1 of 6')).toBeInTheDocument()
    })

    it('offers no next page on the last page', async () => {
      stubApi(casePage([caseSummary()], { page: 6, page_size: 20, total: 120 }))
      renderPage('?page=6')

      const pagination = await screen.findByRole('navigation', { name: 'Pagination' })
      expect(within(pagination).queryByRole('link', { name: 'Next' })).not.toBeInTheDocument()
      expect(within(pagination).getByRole('link', { name: 'Previous' })).toHaveAttribute(
        'href',
        '/cases?page=5',
      )
    })

    it('keeps the active filters in every page link', async () => {
      stubApi(casePage([caseSummary()], { page: 1, page_size: 20, total: 60 }))
      renderPage('?q=glyfosaat&jurisdiction=NL')

      const pagination = await screen.findByRole('navigation', { name: 'Pagination' })
      expect(within(pagination).getByRole('link', { name: 'Next' })).toHaveAttribute(
        'href',
        '/cases?q=glyfosaat&jurisdiction=NL&page=2',
      )
    })

    it('shows an empty result set as an explanation, not as a broken page', async () => {
      stubApi(casePage([], { page: 1, page_size: 20, total: 0 }))
      renderPage('?q=nothing-matches-this')

      expect(await screen.findByText('No cases match these filters.')).toBeInTheDocument()
      expect(screen.getByRole('heading', { level: 2, name: 'Nothing matched' })).toBeInTheDocument()
      expect(screen.queryByRole('navigation', { name: 'Pagination' })).not.toBeInTheDocument()
      expect(screen.queryByRole('link', { name: /^CSV/ })).not.toBeInTheDocument()
    })
  })

  describe('export', () => {
    it('carries the active filters, and not the paging, into both download links', async () => {
      stubApi(casePage([caseSummary()], { page: 2, page_size: 50, total: 120 }))
      renderPage('?q=glyfosaat&jurisdiction=NL&court=7&page=2&page_size=50')

      const csv = await screen.findByRole('link', { name: /^CSV/ })
      const json = screen.getByRole('link', { name: /^JSON Lines/ })

      const csvUrl = new URL(csv.getAttribute('href') ?? '', 'http://localhost')
      expect(csvUrl.pathname).toBe('/api/cases/export')
      expect(csvUrl.searchParams.get('q')).toBe('glyfosaat')
      expect(csvUrl.searchParams.getAll('jurisdiction')).toEqual(['NL'])
      expect(csvUrl.searchParams.get('court')).toBe('7')
      expect(csvUrl.searchParams.get('format')).toBe('csv')
      expect(csvUrl.searchParams.get('page')).toBeNull()
      expect(csvUrl.searchParams.get('page_size')).toBeNull()

      expect(new URL(json.getAttribute('href') ?? '', 'http://localhost').searchParams.get('format')).toBe(
        'jsonl',
      )
    })
  })

  it('says so when the results cannot be loaded', async () => {
    mockApi({
      [CASES]: { status: 503, body: { error: { code: 'unavailable', message: 'Try later.' } } },
      [FILTERS]: { body: filterFacets() },
    })
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('Try later.')
  })

  it('keeps working when the facet values cannot be loaded', async () => {
    mockApi({
      [CASES]: { body: casePage([caseSummary()], { total: 1 }) },
      [FILTERS]: { status: 500, body: { error: { code: 'boom', message: 'No facets.' } } },
    })
    renderPage()

    expect(await screen.findByText('1 case in the collection.')).toBeInTheDocument()
    expect(screen.getByText(/filter values could not be loaded/)).toBeInTheDocument()
    expect(screen.getByRole('searchbox', { name: 'Search' })).toBeInTheDocument()
  })
})
