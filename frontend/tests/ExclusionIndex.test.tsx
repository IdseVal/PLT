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

import ExclusionIndex from '@/components/ExclusionIndex'
import type { ExclusionsResponse } from '@/types/api'

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

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('exclusion index', () => {
  it('asks the API for the criteria', async () => {
    stubJson(PAYLOAD)
    render(<ExclusionIndex />)

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledTimes(1)
    })
    const [url] = vi.mocked(fetch).mock.calls[0] as [string]
    expect(String(url)).toContain('/exclusions')
  })

  it('keeps the three mechanisms apart', async () => {
    stubJson(PAYLOAD)
    render(<ExclusionIndex />)

    expect(await screen.findByText(/terms deliberately left off the lists/i)).toBeInTheDocument()
    expect(screen.getByText(/gated terms/i)).toBeInTheDocument()
    expect(screen.getByText(/exclusion patterns/i)).toBeInTheDocument()
  })

  it('lists a rejected term with the reason it was rejected', async () => {
    stubJson(PAYLOAD)
    render(<ExclusionIndex />)

    const details = await openDisclosure(/terms deliberately left off the lists/i)

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
    render(<ExclusionIndex />)

    const details = await openDisclosure(/^gated terms/i)

    expect(within(details).getByText('talk')).toBeInTheDocument()
    expect(within(details).getByText(/gewasbeschermingsmiddel/)).toBeInTheDocument()
  })

  it('says so when a jurisdiction has nothing under a mechanism', async () => {
    stubJson(PAYLOAD)
    render(<ExclusionIndex />)

    const details = await openDisclosure(/^exclusion patterns/i)

    expect(within(details).getByText('in een opwelling van drift')).toBeInTheDocument()
    expect(
      within(details).getByText(/no exclusion pattern is applied in this jurisdiction/i),
    ).toBeInTheDocument()
  })

  it('counts the entries in each summary', async () => {
    stubJson(PAYLOAD)
    render(<ExclusionIndex />)

    // The heading and the count are separate elements, so the summary is read as a whole.
    const rejected = await screen.findByText(/terms deliberately left off the lists/i)
    // Three rejected terms across the two jurisdictions.
    expect(rejected.closest('summary')).toHaveTextContent('3 entries')
    expect(screen.getByText(/^exclusion patterns/i).closest('summary')).toHaveTextContent(
      '1 entry',
    )
  })

  it('reports a failed request and recovers on retry', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
    render(<ExclusionIndex />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be loaded/i)

    stubJson(PAYLOAD)
    await user.click(screen.getByRole('button', { name: /try again/i }))

    expect(await screen.findByText(/terms deliberately left off the lists/i)).toBeInTheDocument()
  })

  it('says the criteria are unavailable when the payload carries no jurisdiction', async () => {
    stubJson({ jurisdictions: [] })
    render(<ExclusionIndex />)

    expect(await screen.findByText(/not available right now/i)).toBeInTheDocument()
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
    const { container } = render(<ExclusionIndex />)

    await openDisclosure(/terms deliberately left off the lists/i)

    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText(/onerror/)).toBeInTheDocument()
  })
})
