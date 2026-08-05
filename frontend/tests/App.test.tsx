import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '@/App'
import { SITE_MENU } from '@/content/navigation'
import { mockApi } from './helpers/api'

function renderAt(path: string): void {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

// `/cases` and the case route read from the API. The shell is what is under test here, so
// every endpoint answers 404 and the pages render their loading or not-found state; no test
// in this file touches the network.
beforeEach(() => {
  mockApi({})
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('App', () => {
  it('renders the PLT title on the home page', () => {
    renderAt('/')

    expect(
      screen.getByRole('heading', { level: 1, name: /Pesticide Litigation Tracker \(PLT\)/i }),
    ).toBeInTheDocument()
  })

  it('renders the site menu on every route', () => {
    renderAt('/methodology')

    const nav = screen.getByRole('navigation', { name: 'Main' })
    for (const item of SITE_MENU) {
      expect(within(nav).getByRole('link', { name: item.label })).toBeInTheDocument()
    }
  })

  it.each([
    ['/cases', /All cases/i],
    // The detail page names itself after the identifier in the address bar until the case
    // itself arrives, so a reader always knows which case is loading.
    ['/cases/NL/ECLI:NL:HR:2024:1', /^ECLI:NL:HR:2024:1$/],
    ['/about', /About Wageningen Law/i],
    ['/methodology', /Methodology/i],
    ['/faq', /Frequently asked questions/i],
    ['/contact', /Contact/i],
  ])('renders the page behind %s', (path, heading) => {
    renderAt(path)

    expect(screen.getByRole('heading', { level: 1, name: heading })).toBeInTheDocument()
  })

  it('falls back to a not-found page for an unknown route', () => {
    renderAt('/no-such-page')

    expect(screen.getByRole('heading', { level: 1, name: /Page not found/i })).toBeInTheDocument()
  })

  it('gives every route a distinct document title', () => {
    renderAt('/methodology')
    expect(document.title).toBe('Methodology · Pesticide Litigation Tracker')

    cleanup()
    renderAt('/')
    expect(document.title).toBe('Pesticide Litigation Tracker')

    cleanup()
    renderAt('/no-such-page')
    expect(document.title).toBe('Page not found · Pesticide Litigation Tracker')
  })

  it('provides the landmark structure the whole site is read through', () => {
    renderAt('/about')

    expect(screen.getByRole('banner')).toBeInTheDocument()
    expect(screen.getByRole('contentinfo')).toBeInTheDocument()
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main')
    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main')

    // Every navigation landmark is named, so they are distinguishable in a landmark list.
    const navigations = screen.getAllByRole('navigation')
    expect(navigations.length).toBeGreaterThan(1)
    for (const nav of navigations) {
      expect(nav).toHaveAccessibleName()
    }
  })

  it('marks the current page in the menu', () => {
    renderAt('/faq')

    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: 'FAQ' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Contact' })).not.toHaveAttribute('aria-current')
  })

  it('repeats the site navigation and the attribution in the footer', () => {
    renderAt('/')

    const footer = screen.getByRole('contentinfo')
    const footerNav = within(footer).getByRole('navigation', { name: 'Footer' })
    for (const item of SITE_MENU) {
      expect(within(footerNav).getByRole('link', { name: item.label })).toBeInTheDocument()
    }
    expect(within(footer).getByRole('heading', { name: 'Open access' })).toBeInTheDocument()
    expect(within(footer).getByText('Wageningen Law')).toBeInTheDocument()
    expect(within(footer).getByText(/© \d{4} Wageningen Law\./)).toBeInTheDocument()
  })

  it('attributes the site to Wageningen Law rather than to the university', () => {
    renderAt('/')

    expect(screen.getByRole('banner').textContent).toContain('Wageningen Law')
    expect(document.body.textContent).not.toContain('Wageningen University')
  })
})
