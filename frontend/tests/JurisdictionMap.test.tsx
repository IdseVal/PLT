/**
 * The jurisdiction map: coverage, hover, click-through, keyboard access and the zero state.
 *
 * No test here touches the network. `fetch` is stubbed per test so that the real
 * `src/api/client.ts` path runs, and the payloads are the shape `docs/architecture.md`
 * section 5.1 fixes — a bare array, one entry per jurisdiction, zero-case ones included.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import JurisdictionMap from '@/components/JurisdictionMap'
import { JURISDICTION_SHAPES } from '@/components/map/geometry.generated'
import type { JurisdictionStat } from '@/types/api'

/** The endpoint the map is allowed to call, and the only one. */
const STATS_URL = '/api/stats/jurisdictions'

/**
 * What `cleanInlineText` exists to remove and React does not: control characters and
 * bidirectional overrides. Written here rather than imported, so the assertion states the
 * property independently of the implementation it is checking.
 */
// eslint-disable-next-line no-control-regex
const UNSAFE_CHARACTERS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/

/**
 * One entry of the map payload, with everything section 5.1 requires.
 *
 * @param code - Jurisdiction code, which is also its `map_feature_id`.
 * @param name - Jurisdiction name.
 * @param caseCount - How many cases it holds.
 * @returns The entry.
 */
function stat(code: string, name: string, caseCount: number): JurisdictionStat {
  return {
    code,
    name,
    type: code === 'EU' ? 'supranational' : 'state',
    map_feature_id: code,
    is_active: true,
    case_count: caseCount,
    latest_decision_date: caseCount > 0 ? '2024-05-01' : null,
  }
}

/**
 * Stub `fetch` with a fixed JSON body.
 *
 * @param body - Response body to return.
 * @param status - HTTP status. Defaults to 200.
 * @returns The mock, for asserting on the requests made.
 */
function stubJson(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
    ),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

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
 * Render the map inside a router, with a location probe beside it.
 *
 * @returns The user-event instance for driving the interaction.
 */
function renderMap(): ReturnType<typeof userEvent.setup> {
  const user = userEvent.setup()
  render(
    <MemoryRouter initialEntries={['/']}>
      <JurisdictionMap />
      <LocationProbe />
    </MemoryRouter>,
  )
  return user
}

/** The map section, for scoping a query to it. */
function map(): HTMLElement {
  return screen.getByRole('region', { name: /map of jurisdictions/i })
}

/** Current location as rendered by {@link LocationProbe}. */
function currentLocation(): string {
  return screen.getByTestId('location').textContent ?? ''
}

/**
 * One jurisdiction's link.
 *
 * @param name - Accessible name, or part of it.
 * @returns The link element.
 */
function jurisdiction(name: RegExp): Promise<HTMLElement> {
  return within(map()).findByRole('link', { name })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('coverage', () => {
  it('draws every jurisdiction of Annex 2, plus the European Union', async () => {
    stubJson([])
    renderMap()

    await jurisdiction(/^Netherlands:/)

    const links = within(map()).getAllByRole('link')
    expect(links).toHaveLength(JURISDICTION_SHAPES.length + 1)
    expect(links.map((link) => link.getAttribute('data-jurisdiction'))).toContain('EU')
  })

  it('names each jurisdiction and its count for a screen reader', async () => {
    stubJson([stat('NL', 'Netherlands', 42), stat('EU', 'European Union', 1)])
    renderMap()

    expect(await jurisdiction(/^Netherlands: 42 cases$/)).toBeInTheDocument()
    // One case, not "1 cases".
    expect(await jurisdiction(/^European Union: 1 case$/)).toBeInTheDocument()
  })

  it('keeps the European Union separate from its member states', async () => {
    stubJson([stat('NL', 'Netherlands', 42), stat('EU', 'European Union', 7)])
    renderMap()

    // Neither count absorbs the other: the Union is a jurisdiction, not a total.
    expect(await jurisdiction(/^Netherlands: 42 cases$/)).toBeInTheDocument()
    expect(await jurisdiction(/^European Union: 7 cases$/)).toBeInTheDocument()
  })

  it('explains the shading in a legend', async () => {
    stubJson([])
    renderMap()
    await jurisdiction(/^Netherlands:/)

    expect(within(map()).getByText('No cases yet')).toBeInTheDocument()
    expect(within(map()).getByText('1–9 cases')).toBeInTheDocument()
    expect(within(map()).getByText('10–99 cases')).toBeInTheDocument()
    expect(within(map()).getByText('100 cases or more')).toBeInTheDocument()
  })
})

describe('the zero state, which at launch is the normal one', () => {
  it('draws every jurisdiction when the database is empty', async () => {
    stubJson([])
    renderMap()

    expect(await jurisdiction(/^Netherlands: no cases yet$/)).toBeInTheDocument()
    expect(await jurisdiction(/^European Union: no cases yet$/)).toBeInTheDocument()
    expect(within(map()).getAllByRole('link')).toHaveLength(JURISDICTION_SHAPES.length + 1)
  })

  it('draws a jurisdiction the payload does not mention rather than dropping it', async () => {
    stubJson([stat('NL', 'Netherlands', 3), stat('EU', 'European Union', 12)])
    renderMap()

    await jurisdiction(/^Netherlands: 3 cases$/)
    // Nothing has been ingested for Portugal, and the API does not list it at all.
    expect(await jurisdiction(/^Portugal: no cases yet$/)).toBeInTheDocument()
  })

  it('shades a jurisdiction with no cases differently from one with some', async () => {
    stubJson([stat('NL', 'Netherlands', 3)])
    renderMap()

    const netherlands = await jurisdiction(/^Netherlands: 3 cases$/)
    const portugal = await jurisdiction(/^Portugal: no cases yet$/)

    const fillOf = (link: HTMLElement): string =>
      link.querySelector('path')?.getAttribute('class') ?? ''
    expect(fillOf(netherlands)).not.toBe(fillOf(portugal))
    expect(fillOf(portugal)).toContain('fill-plt-accent-soft')
  })
})

describe('a payload that breaks its own contract', () => {
  it('keeps a jurisdiction whose map_feature_id is missing, resolving it by code', async () => {
    // `map_feature_id` is NOT NULL from migration 0006, so this is a violation — but the
    // entry named and counted itself, and section 3 makes `code` the same value for a state.
    stubJson([{ ...stat('NL', 'Netherlands', 3), map_feature_id: null }])
    renderMap()

    expect(await jurisdiction(/^Netherlands: 3 cases$/)).toBeInTheDocument()
  })

  it('degrades to the zero state when no entry carries one, rather than throwing the map', async () => {
    // The one case where dropping and keeping diverge: every entry unusable meant the
    // contract-mismatch guard fired and the reader lost the whole map.
    stubJson([
      { ...stat('NL', 'Netherlands', 3), map_feature_id: null },
      { ...stat('EU', 'European Union', 12), map_feature_id: null },
    ])
    renderMap()

    expect(await jurisdiction(/^Netherlands: 3 cases$/)).toBeInTheDocument()
    expect(await jurisdiction(/^European Union: 12 cases$/)).toBeInTheDocument()
    expect(within(map()).queryByRole('alert')).not.toBeInTheDocument()
  })

  it('strips control characters and bidirectional overrides from a jurisdiction name', async () => {
    // A jurisdiction name is server-supplied text: seeded by us today, but a future
    // jurisdiction taken from a source vocabulary would arrive exactly as a court name does.
    // U+202E reverses what follows it on screen and U+0007 is invisible; React strips
    // neither, because neither is markup.
    stubJson([stat('NL', 'Nether\u0007lands\u202Egnidaelsim\u202C', 3)])
    const user = renderMap()

    const netherlands = await jurisdiction(/^Netherlands: 3 cases$/)
    expect(netherlands.getAttribute('aria-label')).toBe('Netherlandsgnidaelsim: 3 cases')

    await user.hover(netherlands)
    const shown = within(map()).getAllByText(/gnidaelsim/)
    expect(shown.length).toBeGreaterThan(0)
    for (const node of shown) {
      expect(node.textContent ?? '').not.toMatch(UNSAFE_CHARACTERS)
    }
  })

  it('falls back to the repo-authored name when the API sends one that cleans away', async () => {
    stubJson([stat('NL', '\u202E\u202C', 3)])
    renderMap()

    expect(await jurisdiction(/^Netherlands: 3 cases$/)).toBeInTheDocument()
  })
})

describe('counts spanning orders of magnitude', () => {
  it('shades one, tens, hundreds and thousands distinguishably', async () => {
    stubJson([
      stat('MT', 'Malta', 0),
      stat('PT', 'Portugal', 4),
      stat('FR', 'France', 87),
      stat('DE', 'Germany', 640),
      stat('NL', 'Netherlands', 12_500),
    ])
    renderMap()

    await jurisdiction(/^Netherlands: 12,500 cases$/)

    const fillOf = async (name: RegExp): Promise<string> =>
      (await jurisdiction(name)).querySelector('path')?.getAttribute('class') ?? ''

    const bands = [
      await fillOf(/^Malta: no cases yet$/),
      await fillOf(/^Portugal: 4 cases$/),
      await fillOf(/^France: 87 cases$/),
      await fillOf(/^Germany: 640 cases$/),
    ]
    expect(new Set(bands).size).toBe(4)
    // The largest collection shares the darkest band with the merely large one, rather than
    // running off the end of the palette.
    expect(await fillOf(/^Netherlands: 12,500 cases$/)).toBe(bands[3])
  })
})

describe('requests', () => {
  it('asks once, on mount', async () => {
    const fetchMock = stubJson([stat('NL', 'Netherlands', 42)])
    renderMap()

    await jurisdiction(/^Netherlands: 42 cases$/)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(STATS_URL)
  })

  it('asks for nothing further when jurisdictions are hovered', async () => {
    const fetchMock = stubJson([stat('NL', 'Netherlands', 42)])
    const user = renderMap()

    await user.hover(await jurisdiction(/^Netherlands: 42 cases$/))
    await user.hover(await jurisdiction(/^France: no cases yet$/))
    await user.hover(await jurisdiction(/^European Union: no cases yet$/))

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe('the tooltip', () => {
  it('names the jurisdiction and its count on hover, and clears when the pointer leaves', async () => {
    stubJson([stat('NL', 'Netherlands', 42)])
    const user = renderMap()

    const netherlands = await jurisdiction(/^Netherlands: 42 cases$/)
    await user.hover(netherlands)

    const tooltip = within(map()).getByText('42 cases')
    expect(tooltip).toBeInTheDocument()
    expect(within(map()).getAllByText('Netherlands').length).toBeGreaterThan(0)

    await user.unhover(netherlands)
    await waitFor(() => {
      expect(within(map()).queryByText('42 cases')).not.toBeInTheDocument()
    })
  })

  it('is hidden from assistive technology, which reads the shape itself', async () => {
    stubJson([stat('NL', 'Netherlands', 42)])
    const user = renderMap()

    await user.hover(await jurisdiction(/^Netherlands: 42 cases$/))

    const tooltip = within(map()).getByText('42 cases').closest('g')
    expect(tooltip).toHaveAttribute('aria-hidden', 'true')
    // It must never come between the pointer and a shape: that is what makes a tooltip
    // flicker along a border.
    expect(tooltip?.getAttribute('class')).toContain('pointer-events-none')
  })

  it('follows the pointer from one jurisdiction to the next without going blank between', async () => {
    stubJson([stat('NL', 'Netherlands', 42), stat('BE', 'Belgium', 5)])
    const user = renderMap()

    await user.hover(await jurisdiction(/^Netherlands: 42 cases$/))
    expect(within(map()).getByText('42 cases')).toBeInTheDocument()

    await user.hover(await jurisdiction(/^Belgium: 5 cases$/))
    expect(within(map()).getByText('5 cases')).toBeInTheDocument()
    expect(within(map()).queryByText('42 cases')).not.toBeInTheDocument()
  })
})

describe('click-through', () => {
  it('opens the jurisdiction’s cases when a country is clicked', async () => {
    stubJson([stat('NL', 'Netherlands', 42)])
    const user = renderMap()

    await user.click(await jurisdiction(/^Netherlands: 42 cases$/))

    expect(currentLocation()).toBe('/cases?jurisdiction=NL')
  })

  it('opens the Union’s cases when the North Sea marker is clicked', async () => {
    stubJson([stat('EU', 'European Union', 12)])
    const user = renderMap()

    await user.click(await jurisdiction(/^European Union: 12 cases$/))

    expect(currentLocation()).toBe('/cases?jurisdiction=EU')
  })

  it('carries a real href, so the link can be opened in a new tab', async () => {
    stubJson([])
    renderMap()

    expect(await jurisdiction(/^Netherlands:/)).toHaveAttribute('href', '/cases?jurisdiction=NL')
    expect(await jurisdiction(/^European Union:/)).toHaveAttribute('href', '/cases?jurisdiction=EU')
  })
})

describe('reaching the map without a pointer', () => {
  it('puts every jurisdiction in the tab order', async () => {
    stubJson([])
    renderMap()
    await jurisdiction(/^Netherlands:/)

    for (const link of within(map()).getAllByRole('link')) {
      expect(link).toHaveAttribute('tabindex', '0')
    }
  })

  it('reaches the first jurisdiction with the Tab key', async () => {
    stubJson([stat('AT', 'Austria', 2)])
    const user = renderMap()
    await jurisdiction(/^Austria: 2 cases$/)

    await user.tab()

    expect(document.activeElement).toBe(await jurisdiction(/^Austria: 2 cases$/))
  })

  it('shows the same tooltip on focus as on hover', async () => {
    stubJson([stat('NL', 'Netherlands', 42)])
    renderMap()

    const netherlands = await jurisdiction(/^Netherlands: 42 cases$/)
    netherlands.focus()

    await waitFor(() => {
      expect(within(map()).getByText('42 cases')).toBeInTheDocument()
    })
  })

  it('opens the cases on Enter', async () => {
    stubJson([stat('NL', 'Netherlands', 42)])
    const user = renderMap()

    const netherlands = await jurisdiction(/^Netherlands: 42 cases$/)
    netherlands.focus()
    await user.keyboard('{Enter}')

    expect(currentLocation()).toBe('/cases?jurisdiction=NL')
  })

  it('opens the cases on Space, which a map shape reads as a control', async () => {
    stubJson([stat('EU', 'European Union', 12)])
    const user = renderMap()

    const union = await jurisdiction(/^European Union: 12 cases$/)
    union.focus()
    await user.keyboard(' ')

    expect(currentLocation()).toBe('/cases?jurisdiction=EU')
  })

  it('announces the counts once they have loaded', async () => {
    stubJson([stat('NL', 'Netherlands', 42), stat('EU', 'European Union', 12)])
    renderMap()

    const status = within(map()).getByRole('status')
    expect(status).toHaveTextContent(/loading the case count/i)

    await waitFor(() => {
      expect(status).toHaveTextContent(
        new RegExp(`2 of ${String(JURISDICTION_SHAPES.length + 1)} jurisdictions hold cases`, 'i'),
      )
    })
  })
})

describe('when the counts cannot be loaded', () => {
  it('says so, keeps the map, and offers a retry', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    renderMap()

    const alert = await within(map()).findByRole('alert')
    expect(alert).toHaveTextContent(/could not be loaded/i)
    expect(alert).toHaveTextContent(/could not reach the case database/i)

    // The map is still drawn, and says of each jurisdiction only what it knows.
    expect(await jurisdiction(/^Netherlands: case count unavailable$/)).toBeInTheDocument()
  })

  it('asks again when the retry is used', async () => {
    let attempts = 0
    const fetchMock = vi.fn().mockImplementation(() => {
      attempts += 1
      if (attempts === 1) return Promise.reject(new TypeError('Failed to fetch'))
      return Promise.resolve(
        new Response(JSON.stringify([stat('NL', 'Netherlands', 42)]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = renderMap()

    await within(map()).findByRole('alert')
    await user.click(within(map()).getByRole('button', { name: /try again/i }))

    expect(await jurisdiction(/^Netherlands: 42 cases$/)).toBeInTheDocument()
    expect(within(map()).queryByRole('alert')).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('treats an unreadable payload as a failure rather than as an empty database', async () => {
    stubJson({ jurisdictions: [] })
    renderMap()

    expect(await within(map()).findByRole('alert')).toHaveTextContent(/could not read/i)
  })
})
