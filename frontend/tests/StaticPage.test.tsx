/**
 * Static-page tests: the renderer, and the copy it renders.
 *
 * The copy assertions are deliberately about substance rather than wording - an editor must
 * be free to rewrite a sentence, but the methodology page may not quietly stop describing
 * the pipeline it is there to describe.
 */

import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import StaticPage from '@/components/StaticPage'
import { aboutPage } from '@/content/about'
import { contactPage } from '@/content/contact'
import { faqPage } from '@/content/faq'
import { methodologyPage } from '@/content/methodology'
import type { StaticPageContent } from '@/types/content'
import { isSafeHref } from '@/utils/links'

const PAGES: readonly StaticPageContent[] = [aboutPage, methodologyPage, faqPage, contactPage]

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
    '%s tells the reader the copy is still a draft',
    (_title, content) => {
      renderPage(content)

      const note = content.editorialNote
      expect(note).toBeDefined()
      expect(screen.getByText(note ?? '')).toBeInTheDocument()
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

    for (const subject of [
      'eur-lex',
      'rechtspraak',
      'celex',
      'ecli',
      'keyword',
      'weight',
      'threshold',
      'week',
      'checkpoint',
      'content manager',
    ]) {
      expect(text).toContain(subject)
    }
  })

  it('states on the methodology page why keyword selection is necessary', () => {
    renderPage(methodologyPage)

    expect(
      screen.getByRole('heading', { level: 2, name: 'Why selection is keyword-based' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { level: 2, name: 'The keyword lists' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Weekly updates' })).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { level: 2, name: 'Deduplication and revisions' }),
    ).toBeInTheDocument()
  })

  it('says that every jurisdiction needs its own keyword list', () => {
    const text = textOf(methodologyPage)

    expect(text).toContain('cannot be added to the tracker until its list exists')
  })

  it('is written as replaceable copy, not as filler', () => {
    for (const page of PAGES) {
      expect(textOf(page)).not.toContain('lorem ipsum')
      expect(page.lead.length).toBeGreaterThan(60)
      expect(page.sections.length).toBeGreaterThan(2)
    }
  })
})
