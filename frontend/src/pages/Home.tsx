/**
 * Home page.
 *
 * PLACEHOLDER composition: the shell issue renders the title and the standfirst only. The
 * search bar (submitting to `/cases?q=…`), the Europe map and the latest-cases sidebar are
 * added by the issues that own them (`docs/architecture.md` section 6), which replace the
 * body of this component.
 */

import { Link } from 'react-router-dom'

import { useDocumentTitle } from '@/hooks/useDocumentTitle'

export default function Home(): JSX.Element {
  useDocumentTitle('')

  return (
    <section className="space-y-6">
      <h1 className="text-plt-accent-deep font-display text-3xl font-bold sm:text-4xl">
        Pesticide Litigation Tracker (PLT)
      </h1>
      <p className="text-plt-muted max-w-prose text-lg leading-relaxed">
        An open-access, automatically updated database of pesticide-related case law from the
        European Union and its member states, by Wageningen Law.
      </p>
      <p className="border-plt-border text-plt-muted max-w-prose border-l-4 pl-4 text-sm">
        The search bar, the map of jurisdictions and the feed of the twenty most recent cases
        arrive in a coming release. In the meantime, the{' '}
        <Link className="text-plt-accent-strong rounded-sm underline underline-offset-4" to="/methodology">
          methodology
        </Link>{' '}
        describes how the collection is assembled.
      </p>
    </section>
  )
}
