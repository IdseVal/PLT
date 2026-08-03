/**
 * Header tests.
 *
 * Two things are being protected here: the menu contract from the brief (these four items,
 * this wording, this order) and the keyboard behaviour of the mobile disclosure panel.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import Header from '@/components/Header'
import { SITE_MENU } from '@/content/navigation'

/** The menu exactly as the design brief fixes it. Changing this is changing the brief. */
const BRIEF_MENU = ['About Wageningen Law', 'Methodology', 'FAQ', 'Contact'] as const

function renderHeader(path = '/'): void {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Header />
      <Routes>
        <Route path="/faq" element={<p>FAQ page</p>} />
        <Route path="*" element={<p>Some page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Header', () => {
  it('renders the four brief menu items, in the brief order', () => {
    renderHeader()

    const nav = screen.getByRole('navigation', { name: 'Main' })
    const labels = within(nav)
      .getAllByRole('link')
      .map((link) => link.textContent)

    expect(labels).toEqual([...BRIEF_MENU])
    expect(SITE_MENU.map((item) => item.label)).toEqual([...BRIEF_MENU])
  })

  it('points each menu item at its route', () => {
    renderHeader()

    const nav = screen.getByRole('navigation', { name: 'Main' })
    for (const item of SITE_MENU) {
      expect(within(nav).getByRole('link', { name: item.label })).toHaveAttribute('href', item.to)
    }
  })

  it('links the branding back to the home page under an accessible name', () => {
    renderHeader('/faq')

    expect(
      screen.getByRole('link', { name: 'Pesticide Litigation Tracker, home page' }),
    ).toHaveAttribute('href', '/')
  })

  it('reaches every menu item with the keyboard, in order, with no trap', async () => {
    const user = userEvent.setup()
    renderHeader()

    await user.tab() // branding
    await user.tab() // menu disclosure button
    for (const label of BRIEF_MENU) {
      await user.tab()
      expect(screen.getByRole('link', { name: label })).toHaveFocus()
    }

    // Tabbing past the last item leaves the header: focus is not held inside the menu.
    await user.tab()
    expect(screen.getByRole('link', { name: 'Contact' })).not.toHaveFocus()
  })

  describe('mobile disclosure', () => {
    it('starts collapsed and is labelled as such', () => {
      renderHeader()

      const toggle = screen.getByRole('button', { name: /menu/i })
      expect(toggle).toHaveAttribute('aria-expanded', 'false')
      expect(toggle).toHaveAttribute('aria-controls', 'site-menu')
      expect(screen.getByRole('navigation', { name: 'Main' })).toHaveAttribute('id', 'site-menu')
    })

    it('opens and closes on click', async () => {
      const user = userEvent.setup()
      renderHeader()
      const toggle = screen.getByRole('button', { name: /menu/i })

      await user.click(toggle)
      expect(toggle).toHaveAttribute('aria-expanded', 'true')

      await user.click(toggle)
      expect(toggle).toHaveAttribute('aria-expanded', 'false')
    })

    it('opens from the keyboard', async () => {
      const user = userEvent.setup()
      renderHeader()
      const toggle = screen.getByRole('button', { name: /menu/i })

      toggle.focus()
      await user.keyboard('{Enter}')
      expect(toggle).toHaveAttribute('aria-expanded', 'true')
    })

    it('closes on Escape and returns focus to the button', async () => {
      const user = userEvent.setup()
      renderHeader()
      const toggle = screen.getByRole('button', { name: /menu/i })

      await user.click(toggle)
      await user.tab()
      expect(screen.getByRole('link', { name: 'About Wageningen Law' })).toHaveFocus()

      await user.keyboard('{Escape}')
      expect(toggle).toHaveAttribute('aria-expanded', 'false')
      expect(toggle).toHaveFocus()
    })

    it('closes when a menu item navigates to another page', async () => {
      const user = userEvent.setup()
      renderHeader()
      const toggle = screen.getByRole('button', { name: /menu/i })

      await user.click(toggle)
      await user.click(screen.getByRole('link', { name: 'FAQ' }))

      expect(screen.getByText('FAQ page')).toBeInTheDocument()
      expect(toggle).toHaveAttribute('aria-expanded', 'false')
    })

    it('hides the panel below the md breakpoint while keeping it open above it', () => {
      renderHeader()

      // The collapsed panel is display:none on small screens and visible from `md` up; the
      // disclosure button is the mirror image. Breakpoint behaviour is expressed in classes
      // because jsdom applies no stylesheet.
      expect(screen.getByRole('navigation', { name: 'Main' }).className).toContain('hidden md:block')
      expect(screen.getByRole('button', { name: /menu/i }).className).toContain('md:hidden')
    })
  })
})
