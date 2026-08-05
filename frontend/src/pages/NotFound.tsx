/**
 * Fallback for unknown routes.
 *
 * A dead end is a bad place to leave a reader, so the page offers the routes most likely to
 * have been wanted rather than an apology alone.
 */

import { Link } from 'react-router-dom'

import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { FOOTER_DATABASE_LINKS, SITE_MENU } from '@/content/navigation'

export default function NotFound(): JSX.Element {
  useDocumentTitle('Page not found')

  return (
    <section className="space-y-6">
      <h1 className="text-plt-accent-deep font-display text-3xl font-bold sm:text-4xl">
        Page not found
      </h1>
      <p className="text-plt-muted max-w-prose leading-relaxed">
        There is nothing at this address. The page may have been moved, or the link that
        brought you here may be out of date. A case is reached through its jurisdiction and
        its source identifier, so a mistyped ECLI or CELEX number will land here too.
      </p>
      <nav aria-label="Suggested pages">
        <ul className="max-w-prose space-y-2">
          {[...FOOTER_DATABASE_LINKS, ...SITE_MENU].map((item) => (
            <li key={item.to}>
              <Link
                className="text-plt-accent-strong rounded-sm font-medium underline underline-offset-4"
                to={item.to}
              >
                {item.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
    </section>
  )
}
