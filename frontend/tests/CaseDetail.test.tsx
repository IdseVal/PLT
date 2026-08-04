/**
 * The case detail page.
 *
 * Two things are being defended here. The first is that a court document is untrusted input:
 * whatever a judgment contains, it reaches the page as text and nothing else, so the tests
 * feed it markup and assert that no element was created. The second is that the page holds
 * up at its edges — an identifier that matches nothing, a case with no text, and a judgment
 * long enough to matter.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import CaseDetail from '@/pages/CaseDetail'
import { caseRecord, mockApi, type MockApi } from './helpers/api'
import type { CaseRecord } from '@/types/api'

/** The identifier used throughout, in the spelling a reader would paste. */
const SOURCE_ID = 'ECLI:NL:HR:2024:1'

/** The API path that identifier resolves to, percent-encoded as the client sends it. */
const CASE_PATH = `/api/cases/NL/${encodeURIComponent(SOURCE_ID)}`

/**
 * Render the page for one identifier.
 *
 * @param sourceId - The identifier in the address bar.
 * @returns The render result, for container-level assertions.
 */
function renderPage(sourceId = SOURCE_ID): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={[`/cases/NL/${encodeURIComponent(sourceId)}`]}>
      <Routes>
        <Route path="/cases/:jurisdiction/:sourceId" element={<CaseDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

/**
 * Serve one case at the expected path.
 *
 * @param overrides - Fields to change on the record.
 * @returns The request log.
 */
function stubCase(overrides: Partial<CaseRecord> = {}): MockApi {
  return mockApi({ [CASE_PATH]: { body: caseRecord(overrides) } })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('CaseDetail', () => {
  it('asks the API for the case under its encoded identifier', async () => {
    const api = stubCase()
    renderPage()

    await screen.findByRole('heading', { level: 1, name: /Stichting Bollenboos/ })
    expect(api.urls[0]?.pathname).toBe(CASE_PATH)
  })

  it('shows the abstract, the classification and the full text', async () => {
    stubCase()
    renderPage()

    expect(
      await screen.findByRole('heading', { level: 1, name: /Stichting Bollenboos/ }),
    ).toBeInTheDocument()

    const abstract = screen.getByRole('region', { name: 'Abstract' })
    expect(within(abstract).getByText(/precautionary principle/)).toBeInTheDocument()

    const classification = screen.getByRole('region', { name: 'Classification' })
    for (const label of [
      'Jurisdiction',
      'Court',
      'Procedure',
      'Date of filing',
      'Date of decision',
      'Case number',
      'Source identifier',
      'Applicant',
      'Defendant',
      'Law domain',
      'Law subfield',
      'Topics',
      'Language',
    ]) {
      expect(within(classification).getByText(label)).toBeInTheDocument()
    }
    expect(within(classification).getByText('Hoge Raad')).toBeInTheDocument()
    expect(within(classification).getByText('5 March 2024')).toBeInTheDocument()
    expect(within(classification).getByText('State of the Netherlands')).toBeInTheDocument()

    expect(await screen.findByText('Eerste overweging.')).toBeInTheDocument()
    expect(screen.getByText('Tweede overweging.')).toBeInTheDocument()
  })

  it('heads a case with no title with its identifier', async () => {
    // Section 5.1 allows `title` to be null. The loading state is headed by the identifier
    // too, so the loaded case is waited for before the heading is read.
    stubCase({ title: null })
    renderPage()

    await screen.findByRole('heading', { level: 2, name: 'Classification' })
    expect(screen.getByRole('heading', { level: 1, name: SOURCE_ID })).toBeInTheDocument()
    expect(document.title).toBe(`${SOURCE_ID} · Pesticide Litigation Tracker`)
  })

  it('links out to the original publication page', async () => {
    stubCase()
    renderPage()

    const link = await screen.findByRole('link', { name: /original publication page/i })
    expect(link).toHaveAttribute(
      'href',
      'https://uitspraken.rechtspraak.nl/details?id=ECLI:NL:HR:2024:1',
    )
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(link).toHaveAttribute('target', '_blank')
  })

  it('marks the text with the language the court issued it in', async () => {
    stubCase()
    const { container } = renderPage()

    await waitFor(() => {
      expect(container.querySelector('[lang="nl"]')).not.toBeNull()
    })
  })

  it('shows a not-found page for an identifier no case has', async () => {
    mockApi({})
    renderPage('ECLI:NL:HR:9999:9')

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Case not found' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/ECLI:NL:HR:9999:9/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Browse all cases' })).toHaveAttribute('href', '/cases')
    expect(document.title).toBe('Case not found · Pesticide Litigation Tracker')
  })

  it('reports a failure that is not a missing case without losing the page', async () => {
    mockApi({
      [CASE_PATH]: { status: 500, body: { error: { code: 'boom', message: 'Database asleep.' } } },
    })
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('Database asleep.')
    expect(screen.getByRole('link', { name: 'Browse all cases' })).toBeInTheDocument()
  })

  it('says so when the tracker holds no text for a case', async () => {
    stubCase({ documents: [] })
    renderPage()

    expect(await screen.findByText(/holds no full text for this case/)).toBeInTheDocument()
  })

  it('offers a choice when a case has more than one readable document', async () => {
    stubCase({
      language: 'nl',
      documents: [
        { id: 2, language: 'en', doc_type: 'opinion', full_text: 'Opinion of the Advocate General.' },
        { id: 1, language: 'nl', doc_type: 'judgment', full_text: 'Het arrest.' },
      ],
    })
    renderPage()

    // The judgment in the case's own language is what a reader wants first.
    expect(await screen.findByText('Het arrest.')).toBeInTheDocument()

    const chooser = screen.getByRole('combobox', { name: 'Document' })
    expect(within(chooser).getByRole('option', { name: 'Judgment (NL)' })).toBeInTheDocument()
    expect(within(chooser).getByRole('option', { name: 'Opinion (EN)' })).toBeInTheDocument()
  })

  describe('untrusted court text', () => {
    it('renders markup in a judgment as text and creates no element from it', async () => {
      // Nothing in this payload is authored by the project: it is what a compromised or
      // sloppy source could publish.
      stubCase({
        title: '<img src=x onerror="alert(1)">Case of the tags',
        abstract: '<b>Bold</b> claim, <a href="javascript:alert(1)">link</a>.',
        documents: [
          {
            id: 1,
            language: 'nl',
            doc_type: 'judgment',
            full_text: '<script>alert(1)</script>\n\nParagraph two.',
          },
        ],
      })
      const { container } = renderPage()

      expect(await screen.findByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument()
      expect(screen.getByText(/<b>Bold<\/b> claim/)).toBeInTheDocument()

      expect(container.querySelector('script')).toBeNull()
      expect(container.querySelector('img')).toBeNull()
      expect(container.querySelector('b')).toBeNull()
      expect(container.querySelector('a[href^="javascript:"]')).toBeNull()

      // The tags survive as characters — the heading reads them out literally — and no
      // element on the page carries an event handler from them.
      expect(screen.getByRole('heading', { level: 1 }).textContent).toContain('onerror="alert(1)"')
      for (const element of container.querySelectorAll('*')) {
        expect(element.hasAttribute('onerror')).toBe(false)
        expect(element.hasAttribute('onclick')).toBe(false)
      }
    })

    it('refuses to link to a source URL that is not http or https', async () => {
      stubCase({ source_url: 'javascript:alert(1)' })
      const { container } = renderPage()

      expect(await screen.findByText(/no link to an original publication page/)).toBeInTheDocument()
      expect(container.querySelector('a[href^="javascript:"]')).toBeNull()
    })
  })

  it('renders a long judgment in full without a reader having to ask for it', async () => {
    const paragraphs = Array.from({ length: 500 }, (_, index) => `Overweging ${String(index + 1)}.`)
    stubCase({
      documents: [
        { id: 1, language: 'nl', doc_type: 'judgment', full_text: paragraphs.join('\n\n') },
      ],
    })
    renderPage()

    // The first screenful arrives immediately; the rest is revealed across frames, and the
    // whole judgment ends up in the DOM so find-in-page still works on it.
    expect(await screen.findByText('Overweging 1.')).toBeInTheDocument()
    await waitFor(
      () => {
        expect(screen.getByText('Overweging 500.')).toBeInTheDocument()
      },
      { timeout: 5000 },
    )
  })
})
