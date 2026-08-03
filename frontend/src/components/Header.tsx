/**
 * Site header: branding and the site menu, on every route.
 *
 * Scaffold version. The menu items are fixed by `docs/architecture.md` section 6; WUR Law
 * group branding (logo, wordmark) is added by the header issue. Colours come from the
 * Tailwind theme tokens - never a hex value in this file.
 */

import { NavLink } from 'react-router-dom'

interface MenuItem {
  readonly label: string
  readonly to: string
}

const MENU: readonly MenuItem[] = [
  { label: 'About Wageningen Law', to: '/about' },
  { label: 'Methodology', to: '/methodology' },
  { label: 'FAQ', to: '/faq' },
  { label: 'Contact', to: '/contact' },
]

export default function Header(): JSX.Element {
  return (
    <header className="border-wur-border bg-wur-white border-b">
      <div className="mx-auto flex max-w-content flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
        <NavLink to="/" className="text-wur-green-darker text-lg font-bold">
          PLT<span className="text-wur-muted font-normal"> · Wageningen University Law</span>
        </NavLink>
        <nav aria-label="Main">
          <ul className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
            {MENU.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  className={({ isActive }) =>
                    isActive ? 'text-wur-green-dark font-semibold' : 'text-wur-muted hover:text-wur-ink'
                  }
                >
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
