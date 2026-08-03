/**
 * Site header: branding and the site menu, on every route.
 *
 * The menu items come from `src/content/navigation.ts` and are fixed by the brief. Colours
 * come from the Tailwind theme tokens - never a hex value in this file
 * (`docs/architecture.md` section 6).
 *
 * Below the `md` breakpoint the menu collapses behind a disclosure button. The panel is a
 * plain disclosure rather than a modal dialog: focus is never trapped inside it, Escape
 * closes it and returns focus to the button, and a route change closes it. There is exactly
 * one `nav` landmark and one copy of each link at every viewport width, so assistive
 * technology never sees the menu twice.
 */

import { useEffect, useRef, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'

import { SITE_MENU } from '@/content/navigation'

/** Id linking the disclosure button to the panel it controls. */
const MENU_PANEL_ID = 'site-menu'

/**
 * Hamburger / close icon for the mobile disclosure button.
 *
 * @param props - Component properties.
 * @param props.open - Whether the menu is currently open, which selects the glyph.
 * @returns A decorative icon, hidden from assistive technology.
 */
function MenuIcon({ open }: { readonly open: boolean }): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      className="h-5 w-5"
    >
      {open ? (
        <path d="M6 6l12 12M18 6L6 18" />
      ) : (
        <path d="M4 7h16M4 12h16M4 17h16" />
      )}
    </svg>
  )
}

/**
 * Classes for a menu link, varying with the active state.
 *
 * @param isActive - Whether the link matches the current route.
 * @returns The class list for the link.
 */
function menuLinkClasses(isActive: boolean): string {
  const base =
    'block rounded-sm border-l-2 py-2 pl-3 text-sm md:border-l-0 md:border-b-2 md:py-1 md:pl-0 md:pb-1'
  return isActive
    ? `${base} border-plt-accent text-plt-accent-deep font-semibold`
    : `${base} border-transparent text-plt-muted hover:text-plt-accent-deep hover:border-plt-border`
}

/**
 * The site header.
 *
 * @returns The header landmark, rendered on every route.
 */
export default function Header(): JSX.Element {
  const [isOpen, setIsOpen] = useState(false)
  const toggleRef = useRef<HTMLButtonElement>(null)
  const { pathname } = useLocation()

  // A route change closes the mobile panel, so the next page is not covered by the menu.
  useEffect(() => {
    setIsOpen(false)
  }, [pathname])

  /**
   * Close the panel on Escape and hand focus back to the control that opened it.
   *
   * @param event - The keyboard event bubbling out of the header.
   */
  const handleKeyDown = (event: React.KeyboardEvent<HTMLElement>): void => {
    if (event.key !== 'Escape' || !isOpen) return
    setIsOpen(false)
    toggleRef.current?.focus()
  }

  return (
    <header className="border-plt-border bg-plt-panel border-b" onKeyDown={handleKeyDown}>
      {/* Decorative brand band. Carries no information, so it is hidden from screen readers. */}
      <div className="bg-plt-accent-deep h-1.5 w-full" aria-hidden="true" />

      <div className="mx-auto flex max-w-content flex-wrap items-center justify-between gap-x-4 px-4 py-3">
        <NavLink to="/" className="flex items-center gap-3" aria-label="Pesticide Litigation Tracker, home page">
          <span
            aria-hidden="true"
            className="bg-plt-accent-deep text-plt-panel flex h-9 w-9 shrink-0 items-center justify-center rounded-sm text-xs font-bold tracking-wide"
          >
            PLT
          </span>
          <span className="leading-tight">
            <span className="text-plt-accent-deep font-display block text-base font-bold sm:text-lg">
              Pesticide Litigation Tracker
            </span>
            <span className="text-plt-muted block text-xs">Wageningen Law</span>
          </span>
        </NavLink>

        <button
          ref={toggleRef}
          type="button"
          className="border-plt-border text-plt-ink hover:border-plt-accent-strong flex items-center gap-2 rounded-sm border px-3 py-2 text-sm md:hidden"
          aria-expanded={isOpen}
          aria-controls={MENU_PANEL_ID}
          onClick={() => {
            setIsOpen((open) => !open)
          }}
        >
          <MenuIcon open={isOpen} />
          Menu
        </button>

        <nav
          id={MENU_PANEL_ID}
          aria-label="Main"
          className={
            isOpen ? 'w-full pb-1 pt-3 md:w-auto md:pb-0 md:pt-0' : 'hidden md:block md:w-auto'
          }
        >
          <ul className="flex flex-col gap-y-1 md:flex-row md:items-center md:gap-x-6">
            {SITE_MENU.map((item) => (
              <li key={item.to}>
                <NavLink to={item.to} className={({ isActive }) => menuLinkClasses(isActive)}>
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </header>
  )
}
