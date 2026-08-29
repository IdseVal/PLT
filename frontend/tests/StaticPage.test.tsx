/**
 * Static-page tests: the renderer, and the copy it renders.
 *
 * The copy assertions are deliberately about substance rather than wording - an editor must
 * be free to rewrite a sentence, but the methodology page may not quietly stop describing
 * the pipeline it is there to describe.
 */

import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import StaticPage from '@/components/StaticPage'
import { aboutPage } from '@/content/about'
import { contactPage } from '@/content/contact'
import { faqPage } from '@/content/faq'
import { methodologyPage } from '@/content/methodology'
import type { StaticPageContent } from '@/types/content'
import { isSafeHref } from '@/utils/links'

const PAGES: readonly StaticPageContent[] = [aboutPage, methodologyPage, faqPage, contactPage]

// The methodology page embeds the keyword index, which requests `GET /api/filters` on
// mount. These tests are about the renderer and the copy, so the request is left pending:
// the index stays in its loading state and no state update lands after a test has finished.
// The index's own states are covered in `tests/KeywordIndex.test.tsx`.
beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Promise<Response>(() => undefined)),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function renderPage(content: StaticPageContent): void {
  render(
    <MemoryRouter>
      <StaticPage content={content} />
    </MemoryRouter>,
  )
}

/** All the prose on a page, flattened, for substance checks. */
function textOf(content: StaticPageContent): string {
  return JSON.stringify(content).toLowerCase()
}

describe('StaticPage', () => {
  it.each(PAGES.map((page) => [page.title, page] as const))(
    '%s has one h1, a lead and a heading per section',
    (_title, content) => {
      renderPage(content)

      expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
      expect(screen.getByRole('heading', { level: 1, name: content.title })).toBeInTheDocument()
      expect(screen.getByText(content.lead)).toBeInTheDocument()
      for (const section of content.sections) {
        expect(screen.getByRole('heading', { level: 2, name: section.heading })).toBeInTheDocument()
      }
    },
  )

  it.each(PAGES.map((page) => [page.title, page] as const))(
    '%s renders its editorial note while it has one',
    (_title, content) => {
      renderPage(content)

      // Provisional copy carries a note saying so; copy the Law group has signed off does
      // not. Requiring one would make finishing a page fail the build.
      const note = content.editorialNote
      if (note === undefined) return
      expect(screen.getByText(note)).toBeInTheDocument()
    },
  )

  it('offers a jump list on a long page and anchors every section', () => {
    renderPage(methodologyPage)

    const jumpList = screen.getByRole('navigation', { name: 'On this page' })
    for (const section of methodologyPage.sections) {
      expect(within(jumpList).getByRole('link', { name: section.heading })).toHaveAttribute(
        'href',
        `#${section.id}`,
      )
      expect(document.getElementById(section.id)).not.toBeNull()
    }
  })

  it('opens external links safely and keeps internal links in the router', () => {
    renderPage(aboutPage)

    const external = screen.getByRole('link', { name: 'Sabin Center climate litigation databases' })
    expect(external).toHaveAttribute('target', '_blank')
    expect(external).toHaveAttribute('rel', 'noopener noreferrer')

    const internal = screen.getByRole('link', { name: 'How cases are collected and selected' })
    expect(internal).toHaveAttribute('href', '/methodology')
    expect(internal).not.toHaveAttribute('target')
  })

  it('renders an email link without opening a new tab', () => {
    renderPage(contactPage)

    const email = screen.getByRole('link', { name: 'plt@wur.nl' })
    expect(email).toHaveAttribute('href', 'mailto:plt@wur.nl')
    expect(email).not.toHaveAttribute('target')
  })

  it('drops a link target that is not a safe absolute URL', () => {
    renderPage({
      title: 'Test page',
      lead: 'Lead.',
      sections: [
        {
          id: 'links',
          heading: 'Links',
          blocks: [
            {
              kind: 'links',
              items: [
                { label: 'Dangerous', href: 'javascript:alert(1)' },
                { label: 'Relative', href: '/cases' },
                { label: 'Safe', href: 'https://example.org/' },
              ],
            },
          ],
        },
      ],
    })

    expect(screen.queryByText('Dangerous')).not.toBeInTheDocument()
    expect(screen.queryByText('Relative')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Safe' })).toBeInTheDocument()
  })
})

describe('isSafeHref', () => {
  it.each(['https://example.org/', 'http://example.org/', 'mailto:someone@example.org'])(
    'accepts %s',
    (href) => {
      expect(isSafeHref(href)).toBe(true)
    },
  )

  it.each([
    'javascript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:msgbox(1)',
    '/cases',
    'not a url',
  ])('rejects %s', (href) => {
    expect(isSafeHref(href)).toBe(false)
  })
})

describe('page copy', () => {
  it('keeps the methodology page describing the pipeline that was actually built', () => {
    const text = textOf(methodologyPage)

    // The two sources, the identifier each is keyed on, and the three things the method
    // rests on. Deliberately a short list of subjects rather than of sentences: the copy is
    // the Law group's to write, and a test that pinned its phrasing would fail on an edit
    // that improved it.
    for (const subject of ['eur-lex', 'rechtspraak', 'celex', 'ecli', 'keyword', 'corpus', 'week']) {
      expect(text).toContain(subject)
    }
  })

  it('is structured as a systematic review: corpus, criteria, records, schedule, limits', () => {
    renderPage(methodologyPage)

    expect(screen.getByRole('heading', { level: 2, name: 'The corpus' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Inclusion criteria' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Exclusion criteria' })).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { level: 2, name: 'What each included case records' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Update schedule' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Limitations' })).toBeInTheDocument()
  })

  it('never describes selection as weighted scoring against a threshold', () => {
    const text = textOf(methodologyPage)

    // Phrased as a prohibition rather than as a required sentence: the copy is the Law
    // group's to word, and what has to stay true is that it does not describe a method the
    // pipeline stopped using. A mention of the old approach as history is allowed; a claim
    // that a case is admitted by reaching a score is not.
    for (const claim of ['reaches the threshold', 'total weight', 'min_score', 'review band']) {
      expect(text).not.toContain(claim)
    }
  })

  it('embeds the per-jurisdiction keyword index in the methodology page', () => {
    const blocks = methodologyPage.sections.flatMap((section) => section.blocks)

    expect(blocks.some((block) => block.kind === 'keyword-index')).toBe(true)
  })

  it('binds every exclusion mechanism to the definition that describes it', () => {
    const definitions = methodologyPage.sections
      .flatMap((section) => section.blocks)
      .flatMap((block) => (block.kind === 'definitions' ? block.items : []))

    // Bound by key rather than by wording, so rewriting the copy cannot silently detach a
    // list of excluded terms from the paragraph that introduces it.
    expect(definitions.filter((item) => item.mechanism !== undefined).map((i) => i.mechanism))
      .toEqual(['left-off', 'gated', 'patterns'])
  })

  it('is written as replaceable copy, not as filler', () => {
    for (const page of PAGES) {
      expect(textOf(page)).not.toContain('lorem ipsum')
      expect(page.lead.length).toBeGreaterThan(60)
      expect(page.sections.length).toBeGreaterThan(2)
    }
  })
})
