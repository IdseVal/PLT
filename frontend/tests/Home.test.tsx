/**
 * Home page: composition, search submission and the latest-cases sidebar.
 *
 * No test here touches the network. `fetch` is stubbed per test so that the real
 * `src/api/client.ts` code path runs — the component's contract with the client is part of
 * what is being tested, and mocking the client module would hide it.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import Home from '@/pages/Home'
import type { CaseSummary } from '@/types/api'

/**
 * A case as the API returns it, with everything present.
 *
 * `satisfies` rather than an annotation: `CaseSummary.title` is nullable (section 5.1), and
 * a test that queries by `NL_CASE.title` needs the fixture's own title to stay a string.
 */
const NL_CASE = {
  jurisdiction_code: 'NL',
  jurisdiction_name: 'Netherlands',
  source_id: 'ECLI:NL:HR:2024:1',
  title: 'Stichting Bijenlint v. State of the Netherlands',
  court_name: 'Hoge Raad',
  decision_date: '2024-03-12',
} satisfies CaseSummary

/** A case with the fields a source may leave out. */
const EU_CASE = {
  jurisdiction_code: 'EU',
  jurisdiction_name: null,
  source_id: '62021CJ0616',
  title: 'Commission v. Ireland',
  court_name: null,
  decision_date: null,
} satisfies CaseSummary

/** A case the source published no title for: section 5.1 puts it on the wire as `null`. */
const UNTITLED_CASE = {
  jurisdiction_code: 'NL',
  jurisdiction_name: 'Netherlands',
  source_id: 'ECLI:NL:RBDHA:2024:9',
  title: null,
  court_name: 'Rechtbank Den Haag',
  decision_date: '2024-04-02',
} satisfies CaseSummary

/**
 * Report the current location, so a navigation can be asserted on directly.
 *
 * @returns The path and query string of the current location.
 */
function LocationProbe(): JSX.Element {
  const location = useLocation()
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>
}

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
 * Every URL the stubbed `fetch` was called with.
 *
 * The home page has two independent readers of the API — the latest-cases feed and the map —
 * so a test that cares about one of them names its endpoint rather than counting calls.
 *
 * @param fetchMock - The stub returned by {@link stubJson}.
 * @returns The requested URLs, in call order.
 */
function requestedUrls(fetchMock: ReturnType<typeof vi.fn>): string[] {
  return fetchMock.mock.calls.map((call) => String(call[0]))
}

/**
 * The latest-cases sidebar, for scoping a query to it.
 *
 * The map alongside it also has a status region, an error alert with a retry button and a
 * list, so a bare `getByRole` on the page would match either component.
 *
 * @returns The sidebar element.
 */
function sidebar(): HTMLElement {
  return screen.getByRole('complementary', { name: /latest cases/i })
}

/**
 * Render the home page inside a router, with a location probe beside it.
 *
 * @returns The user-event instance for driving the interaction.
 */
function renderHome(): ReturnType<typeof userEvent.setup> {
  const user = userEvent.setup()
  render(
    <MemoryRouter initialEntries={['/']}>
      <Home />
      <LocationProbe />
    </MemoryRouter>,
  )
  return user
}

/** Current location as rendered by {@link LocationProbe}. */
function currentLocation(): string {
  return screen.getByTestId('location').textContent ?? ''
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('home page composition', () => {
  it('renders the title, the search bar, the map slot and the sidebar', async () => {
    stubJson({ items: [NL_CASE], page: 1, page_size: 20, total: 1 })
    renderHome()

    expect(
      screen.getByRole('heading', { level: 1, name: 'Pesticide Litigation Tracker (PLT)' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('search', { name: /search the case law/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: /map of jurisdictions/i })).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: /latest cases/i })).toBeInTheDocument()

    await screen.findByRole('link', { name: NL_CASE.title })
  })

  it('places the search bar above the map and the map above the sidebar', async () => {
    stubJson({ items: [], page: 1, page_size: 20, total: 0 })
    renderHome()
    await screen.findByText(/no cases have been published yet/i)

    const search = screen.getByRole('search', { name: /search the case law/i })
    const map = screen.getByRole('heading', { level: 2, name: /map of jurisdictions/i })
    const sidebar = screen.getByRole('complementary', { name: /latest cases/i })

    // DOCUMENT_POSITION_FOLLOWING: the second node comes after the first.
    expect(search.compareDocumentPosition(map) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(map.compareDocumentPosition(sidebar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})

describe('home page search', () => {
  it('navigates to the all-cases page with the query, rather than searching in place', async () => {
    stubJson({ items: [], page: 1, page_size: 20, total: 0 })
    const user = renderHome()

    await user.type(screen.getByLabelText(/search the collection/i), 'plant protection')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    expect(currentLocation()).toBe('/cases?q=plant+protection')
    // The home page shows no results of its own.
    expect(screen.queryByRole('heading', { name: /search results/i })).not.toBeInTheDocument()
  })

  it('submits on Enter and encodes what was typed', async () => {
    stubJson({ items: [], page: 1, page_size: 20, total: 0 })
    const user = renderHome()

    await user.type(screen.getByLabelText(/search the collection/i), 'glyfosaat & bijen{Enter}')

    expect(currentLocation()).toBe('/cases?q=glyfosaat+%26+bijen')
  })

  it('goes to the unfiltered listing when nothing was typed', async () => {
    stubJson({ items: [], page: 1, page_size: 20, total: 0 })
    const user = renderHome()

    await user.type(screen.getByLabelText(/search the collection/i), '   ')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    expect(currentLocation()).toBe('/cases')
  })

  it('offers a way through to the whole collection without searching first', async () => {
    // A reader who has no term in mind should not have to invent one to see anything.
    stubJson({ items: [], page: 1, page_size: 20, total: 0 })
    const user = renderHome()

    const search = screen.getByRole('search', { name: /search the case law/i })
    await user.click(within(search).getByRole('link', { name: /browse all cases/i }))

    expect(currentLocation()).toBe('/cases')
  })
})

describe('latest-cases sidebar', () => {
  it('asks the API for twenty cases', async () => {
    const fetchMock = stubJson({ items: [NL_CASE], page: 1, page_size: 20, total: 1 })
    renderHome()

    await screen.findByRole('link', { name: NL_CASE.title })
    // The map requests its own counts, so the feed's request is asserted by URL rather than
    // by position: which effect runs first is not part of either component's contract.
    expect(requestedUrls(fetchMock)).toContain('/api/cases/latest?limit=20')
  })

  it('shows the title, jurisdiction, court and decision date, linking to the case', async () => {
    stubJson({ items: [NL_CASE, EU_CASE], page: 1, page_size: 20, total: 2 })
    renderHome()

    const sidebar = screen.getByRole('complementary', { name: /latest cases/i })
    const link = await within(sidebar).findByRole('link', { name: NL_CASE.title })
    expect(link).toHaveAttribute('href', '/cases/NL/ECLI%3ANL%3AHR%3A2024%3A1')

    const row = link.closest('li')
    expect(row).not.toBeNull()
    expect(row).toHaveTextContent('Netherlands')
    expect(row).toHaveTextContent('Hoge Raad')
    expect(row).toHaveTextContent('12 March 2024')
    expect(within(row as HTMLElement).getByText('12 March 2024')).toHaveAttribute(
      'datetime',
      '2024-03-12',
    )

    // A case without a court or a date still renders, falling back to the jurisdiction code.
    const euLink = within(sidebar).getByRole('link', { name: EU_CASE.title })
    expect(euLink).toHaveAttribute('href', '/cases/EU/62021CJ0616')
    expect(euLink.closest('li')).toHaveTextContent('EU')
  })

  it('keeps the cases in the order the API returned them', async () => {
    stubJson({ items: [NL_CASE, EU_CASE], page: 1, page_size: 20, total: 2 })
    renderHome()

    const sidebar = screen.getByRole('complementary', { name: /latest cases/i })
    await within(sidebar).findByRole('link', { name: NL_CASE.title })

    const titles = within(sidebar)
      .getAllByRole('listitem')
      .map((item) => within(item).getByRole('link').textContent)
    expect(titles).toEqual([NL_CASE.title, EU_CASE.title])
  })

  it('lists a case with no title under its identifier', async () => {
    stubJson({ items: [UNTITLED_CASE], limit: 20 })
    renderHome()

    const link = await within(sidebar()).findByRole('link', { name: UNTITLED_CASE.source_id })
    expect(link).toHaveAttribute('href', '/cases/NL/ECLI%3ANL%3ARBDHA%3A2024%3A9')
    expect(link.closest('li')).toHaveTextContent('Rechtbank Den Haag')
  })

  it('renders twenty cases when the API returned twenty, whatever is null in them', async () => {
    // The defect this guards: a case whose title is null used to be dropped by the client's
    // narrowing, so the sidebar showed fewer rows than the API sent and said nothing about it.
    const items = Array.from({ length: 20 }, (_unused, index) => ({
      ...NL_CASE,
      source_id: `ECLI:NL:HR:2024:${index + 1}`,
      title: index % 2 === 0 ? null : `Case ${index + 1}`,
      jurisdiction_name: null,
      court_name: null,
      decision_date: null,
    }))
    stubJson({ items, limit: 20 })
    renderHome()

    await within(sidebar()).findByRole('link', { name: 'ECLI:NL:HR:2024:1' })
    expect(within(sidebar()).getAllByRole('listitem')).toHaveLength(20)
    expect(within(sidebar()).getByRole('link', { name: 'ECLI:NL:HR:2024:19' })).toBeInTheDocument()
  })

  it('accepts a bare array as well as a paginated envelope', async () => {
    stubJson([NL_CASE])
    renderHome()

    expect(await screen.findByRole('link', { name: NL_CASE.title })).toBeInTheDocument()
  })

  it('announces a loading state before the cases arrive', () => {
    stubJson({ items: [NL_CASE], page: 1, page_size: 20, total: 1 })
    renderHome()

    expect(within(sidebar()).getByRole('status')).toHaveTextContent(/loading the latest cases/i)
  })

  it('explains an empty collection instead of showing an empty box', async () => {
    stubJson({ items: [], page: 1, page_size: 20, total: 0 })
    renderHome()

    expect(await screen.findByText(/no cases have been published yet/i)).toBeInTheDocument()
    expect(within(sidebar()).queryByRole('listitem')).not.toBeInTheDocument()
  })

  it('links through to the all-cases page', async () => {
    stubJson({ items: [], page: 1, page_size: 20, total: 0 })
    renderHome()
    await screen.findByText(/no cases have been published yet/i)

    expect(within(sidebar()).getByRole('link', { name: 'All cases' })).toHaveAttribute('href', '/cases')
  })
})

describe('latest-cases sidebar failure', () => {
  it('keeps the page and offers a retry when the request fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    renderHome()

    const alert = await within(sidebar()).findByRole('alert')
    expect(alert).toHaveTextContent(/could not be loaded/i)
    expect(alert).toHaveTextContent(/could not reach the case database/i)

    // The rest of the page is untouched: a failed feed does not blank the home page.
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument()
    expect(screen.getByRole('search', { name: /search the case law/i })).toBeInTheDocument()
    expect(within(sidebar()).getByRole('link', { name: 'All cases' })).toBeInTheDocument()
  })

  it('reloads the feed when the retry button is used', async () => {
    // The feed's first request fails and its second succeeds. Which request that is has to be
    // decided by URL: the map requests its own data from the same page, and neither component
    // promises to go first.
    let feedRequests = 0
    const fetchMock = vi.fn().mockImplementation((url: unknown) => {
      if (!String(url).startsWith('/api/cases/latest')) {
        return Promise.resolve(
          new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }),
        )
      }
      feedRequests += 1
      if (feedRequests === 1) return Promise.reject(new TypeError('Failed to fetch'))
      return Promise.resolve(
        new Response(JSON.stringify({ items: [NL_CASE], page: 1, page_size: 20, total: 1 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/']}>
        <Home />
      </MemoryRouter>,
    )

    await within(sidebar()).findByRole('alert')
    await user.click(within(sidebar()).getByRole('button', { name: /try again/i }))

    expect(await screen.findByRole('link', { name: NL_CASE.title })).toBeInTheDocument()
    await waitFor(() => {
      expect(within(sidebar()).queryByRole('alert')).not.toBeInTheDocument()
    })
    expect(requestedUrls(fetchMock).filter((url) => url.startsWith('/api/cases/latest'))).toHaveLength(2)
  })

  it('shows the message the API sent when it rejected the request', async () => {
    stubJson({ error: { code: 'validation_error', message: 'limit must be at most 50.' } }, 400)
    renderHome()

    expect(await within(sidebar()).findByRole('alert')).toHaveTextContent('limit must be at most 50.')
  })

  it('treats an unreadable payload as an error rather than as an empty feed', async () => {
    stubJson({ items: [{ nonsense: true }] })
    renderHome()

    const alert = await within(sidebar()).findByRole('alert')
    expect(alert).toHaveTextContent(/could not read/i)
    expect(screen.queryByText(/no cases have been published yet/i)).not.toBeInTheDocument()
  })
})

describe('untrusted court text', () => {
  it('renders a title containing markup as text', async () => {
    const hostile = {
      ...NL_CASE,
      source_id: 'ECLI:NL:HR:2024:2',
      title: '<img src=x onerror="alert(1)"> Anderson v. Registrar',
    } satisfies CaseSummary
    stubJson({ items: [hostile], page: 1, page_size: 20, total: 1 })
    const { container } = render(
      <MemoryRouter initialEntries={['/']}>
        <Home />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('link', { name: hostile.title })).toBeInTheDocument()
    expect(container.querySelector('img')).toBeNull()
  })

  it('percent-encodes an identifier before it reaches an href', async () => {
    const awkward: CaseSummary = {
      ...EU_CASE,
      source_id: 'a/b?c=1',
      title: 'Odd identifier',
    }
    stubJson({ items: [awkward] })
    renderHome()

    expect(await screen.findByRole('link', { name: 'Odd identifier' })).toHaveAttribute(
      'href',
      '/cases/EU/a%2Fb%3Fc%3D1',
    )
  })
})
