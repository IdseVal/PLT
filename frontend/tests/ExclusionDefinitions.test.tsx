/**
 * The exclusion-criteria disclosures on the methodology page.
 *
 * `fetch` is stubbed rather than the client module, so the real `src/api/client.ts` path
 * runs: the component's contract with the client is part of what is under test, and mocking
 * the client would hide it. That is the convention the other component tests here follow.
 *
 * The property that matters is separation. The three mechanisms are different claims about a
 * case — a term that never ran, a term that ran but could not admit alone, a phrase that
 * threw the document out — and a page that merged them would tell a reader less than it
 * appears to.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ExclusionDefinitions from '@/components/ExclusionDefinitions'
import type { ExclusionsResponse } from '@/types/api'
import type { ContentDefinition } from '@/types/content'

/** Two jurisdictions, one of which has nothing under one of the mechanisms. */
const PAYLOAD: ExclusionsResponse = {
  jurisdictions: [
    {
      code: 'NL',
      name: 'Netherlands',
      list_version: '2.5.0',
      excluded_terms: [
        {
          id: 'nl-werkzame-stof',
          term: 'werkzame stof',
          category: 'product_class',
          reason: 'Every chemical, pharmaceutical and food case names an active substance.',
        },
        {
          id: 'nl-water',
          term: 'water',
          category: 'active_substance',
          reason: 'A gate governs inclusion, not what a case is about.',
        },
      ],
      gated_terms: [
        {
          id: 'nl-talk',
          term: 'talk',
          category: 'active_substance',
          requires: ['gewasbeschermingsmiddel'],
        },
      ],
      exclusion_patterns: [
        {
          pattern: 'in een opwelling van drift',
          reason: "Criminal-law idiom; 'drift' as emotion, not spray drift.",
        },
      ],
    },
    {
      code: 'EU',
      name: 'European Union',
      list_version: '2.1.0',
      excluded_terms: [
        {
          id: 'en-reach',
          term: 'REACH',
          category: 'statute',
          reason: 'REACH governs industrial chemicals generally.',
        },
      ],
      gated_terms: [
        { id: 'en-beer', term: 'Beer', category: 'active_substance', requires: ['pesticide'] },
      ],
      exclusion_patterns: [],
    },
  ],
}

/**
 * Stub `fetch` with one JSON response.
 *
 * @param body - The payload to return.
 */
function stubJson(body: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    ),
  )
}

/**
 * Open a disclosure by its accessible name and return its element.
 *
 * @param name - Text of the `<summary>`, matched loosely.
 * @returns The `<details>` element.
 */
async function openDisclosure(name: RegExp): Promise<HTMLElement> {
  const user = userEvent.setup()
  const summary = await screen.findByText(name)
  await user.click(summary)
  const details = summary.closest('details')
  if (details === null) throw new Error('summary is not inside a details element')
  return details
}

/** The three definitions the methodology page binds to the three mechanisms. */
const ITEMS: readonly ContentDefinition[] = [
  {
    term: 'Terms deliberately left off the lists',
    description: 'A candidate term outside the scope is removed.',
    mechanism: 'left-off',
  },
  {
    term: 'Gated terms',
    description: 'An ordinary-word substance stays on the list but cannot admit a case alone.',
    mechanism: 'gated',
  },
  {
    term: 'Exclusion patterns',
    description: 'A phrase trap vetoes a document outright.',
    mechanism: 'patterns',
  },
]

/**
 * Render the definition list with the three mechanisms bound.
 *
 * @returns Nothing; assertions read the screen.
 */
function renderDefinitions(): void {
  render(<ExclusionDefinitions items={ITEMS} />)
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('exclusion index', () => {
  it('asks the API for the criteria', async () => {
    stubJson(PAYLOAD)
    renderDefinitions()

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledTimes(1)
    })
    const [url] = vi.mocked(fetch).mock.calls[0] as [string]
    expect(String(url)).toContain('/exclusions')
  })

  it('puts each disclosure under the definition that describes it', async () => {
    stubJson(PAYLOAD)
    renderDefinitions()

    // The binding that matters: the list of rejected terms belongs to the paragraph about
    // rejected terms, not to a block of three at the foot of the section.
    const rejected = await screen.findByText(/show all 3 rejected terms/i)
    const gated = screen.getByText(/show all 2 gated terms/i)
    const patterns = screen.getByText(/show all 1 exclusion pattern/i)

    expect(rejected.closest('dd')).toHaveTextContent('A candidate term outside the scope')
    expect(gated.closest('dd')).toHaveTextContent('cannot admit a case alone')
    expect(patterns.closest('dd')).toHaveTextContent('vetoes a document outright')
  })

  it('asks for the criteria once, however many mechanisms the page describes', async () => {
    stubJson(PAYLOAD)
    renderDefinitions()

    await screen.findByText(/show all 3 rejected terms/i)
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('lists a rejected term with the reason it was rejected', async () => {
    stubJson(PAYLOAD)
    renderDefinitions()

    const details = await openDisclosure(/show all \d+ rejected terms?/i)

    expect(within(details).getByText('werkzame stof')).toBeInTheDocument()
    expect(
      within(details).getByText(/every chemical, pharmaceutical and food case/i),
    ).toBeInTheDocument()
    // Both jurisdictions appear, each under its own heading.
    expect(within(details).getByRole('heading', { name: 'Netherlands' })).toBeInTheDocument()
    expect(within(details).getByRole('heading', { name: 'European Union' })).toBeInTheDocument()
  })

  it('names the gate a gated term depends on, rather than only flagging it', async () => {
    stubJson(PAYLOAD)
    renderDefinitions()

    const details = await openDisclosure(/show all \d+ gated terms?/i)

    expect(within(details).getByText('talk')).toBeInTheDocument()
    expect(within(details).getByText(/gewasbeschermingsmiddel/)).toBeInTheDocument()
  })

  it('says so when a jurisdiction has nothing under a mechanism', async () => {
    stubJson(PAYLOAD)
    renderDefinitions()

    const details = await openDisclosure(/show all \d+ exclusion patterns?/i)

    expect(within(details).getByText('in een opwelling van drift')).toBeInTheDocument()
    expect(
      within(details).getByText(/no exclusion pattern is applied in this jurisdiction/i),
    ).toBeInTheDocument()
  })

  it('counts the entries in each summary', async () => {
    stubJson(PAYLOAD)
    renderDefinitions()

    // Three rejected terms across the two jurisdictions; one pattern, singular.
    expect(await screen.findByText(/show all 3 rejected terms/i)).toBeInTheDocument()
    expect(screen.getByText(/show all 1 exclusion pattern$/i)).toBeInTheDocument()
  })

  it('reports a failed request and recovers on retry', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
    renderDefinitions()

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be loaded/i)

    stubJson(PAYLOAD)
    await user.click(screen.getByRole('button', { name: /try again/i }))

    expect(await screen.findByText(/show all 3 rejected terms/i)).toBeInTheDocument()
  })

  it('still renders the definitions when the payload carries no jurisdiction', async () => {
    stubJson({ jurisdictions: [] })
    renderDefinitions()

    // The copy is the page's; only the lists come from the API, so an empty payload costs
    // the disclosures and nothing else.
    expect(await screen.findByText('Gated terms')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('renders a hostile reason as text', async () => {
    stubJson({
      jurisdictions: [
        {
          code: 'NL',
          name: 'Netherlands',
          list_version: '2.5.0',
          excluded_terms: [
            {
              id: 'nl-x',
              term: 'nasty',
              category: 'general',
              reason: '<img src=x onerror="alert(1)">',
            },
          ],
          gated_terms: [],
          exclusion_patterns: [],
        },
      ],
    })
    const { container } = render(<ExclusionDefinitions items={ITEMS} />)

    await openDisclosure(/show all \d+ rejected terms?/i)

    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText(/onerror/)).toBeInTheDocument()
  })
})
