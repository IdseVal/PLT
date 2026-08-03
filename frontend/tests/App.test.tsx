import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import App from '@/App'

function renderAt(path: string): void {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

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
    for (const label of ['About Wageningen Law', 'Methodology', 'FAQ', 'Contact']) {
      expect(screen.getByRole('link', { name: label })).toBeInTheDocument()
    }
    expect(nav).toBeInTheDocument()
  })

  it.each([
    ['/cases', /All cases/i],
    ['/cases/NL/ECLI:NL:HR:2024:1', /^Case$/i],
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
})
