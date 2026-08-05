/**
 * Site footer: attribution to Wageningen Law, the open-access statement, and a repeat of the
 * site navigation.
 *
 * Rendered on every route. Colours come from the theme tokens; the dark band needs a lighter
 * focus ring than the rest of the site, which is the `focus-on-dark` class defined in
 * `src/styles/index.css`.
 */

import { Link } from 'react-router-dom'

import { FOOTER_DATABASE_LINKS, SITE_MENU } from '@/content/navigation'

/** Shared classes for links in the footer band. */
const FOOTER_LINK = 'text-plt-panel focus-on-dark rounded-sm underline-offset-4 hover:underline'

/**
 * A link inside a sentence, rather than in a list. It is underlined at rest: a link in a
 * block of text may not be distinguished by colour alone (WCAG 2.1 SC 1.4.1).
 */
const FOOTER_INLINE_LINK = `${FOOTER_LINK} underline`

/** Shared classes for the small headings above each footer column. */
const FOOTER_HEADING = 'text-plt-panel font-display mb-3 text-base font-bold'

/**
 * The site footer.
 *
 * @returns The `contentinfo` landmark, rendered on every route.
 */
export default function Footer(): JSX.Element {
  const year = new Date().getFullYear()

  return (
    <footer className="bg-plt-accent-deep text-plt-inverse-muted mt-16">
      <div className="mx-auto grid max-w-content gap-8 px-4 py-10 sm:grid-cols-2 lg:grid-cols-4">
        <div className="sm:col-span-2">
          <h2 className={FOOTER_HEADING}>Pesticide Litigation Tracker</h2>
          <p className="max-w-prose text-sm leading-relaxed">
            A project of <strong className="text-plt-panel font-semibold">Wageningen Law</strong>,
            the Law group. The tracker collects, classifies and republishes pesticide-related
            case law from the European Union and its member states, and is maintained by the
            group as part of its research on food, agriculture and environmental law.
          </p>
        </div>

        <nav aria-label="Footer">
          <h2 className={FOOTER_HEADING}>The database</h2>
          <ul className="space-y-2 text-sm">
            {[...FOOTER_DATABASE_LINKS, ...SITE_MENU].map((item) => (
              <li key={item.to}>
                <Link className={FOOTER_LINK} to={item.to}>
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        <div>
          <h2 className={FOOTER_HEADING}>Open access</h2>
          <p className="max-w-prose text-sm leading-relaxed">
            The PLT is published open access, without registration or payment, so that it can be
            used freely in academic research and by civil-society organisations. Judgments remain
            the work of the courts that published them and are shown in their original language
            with a link back to the official source; the collection, its abstracts and its
            metadata may be reused with attribution to Wageningen Law.
          </p>
        </div>
      </div>

      <div className="border-plt-accent-strong border-t">
        <div className="mx-auto flex max-w-content flex-col gap-2 px-4 py-4 text-xs sm:flex-row sm:items-center sm:justify-between">
          <p>&copy; {year} Wageningen Law.</p>
          <p>
            Case law is reproduced from official public sources. See the{' '}
            <Link className={FOOTER_INLINE_LINK} to="/methodology">
              methodology
            </Link>{' '}
            for what is and is not included.
          </p>
        </div>
      </div>
    </footer>
  )
}
