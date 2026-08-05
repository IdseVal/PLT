/**
 * Home page.
 *
 * The composition is fixed by `docs/architecture.md` section 6 and core document section
 * 3.3: the title, a prominent search bar directly beneath it, the map below the search bar,
 * and a right-hand sidebar of the twenty most recent cases with a button through to the
 * all-cases page.
 *
 * The email-alert signup is the one addition, and it is placed *after* all four so none of
 * them moves: it is the last thing in the right-hand column, under the sidebar's "All cases"
 * button, where it reads as the email version of the feed above it rather than as a banner
 * competing with the search bar.
 *
 * On narrow viewports the two-column grid collapses to one column, so the sidebar moves
 * below the main column rather than disappearing. It follows the map in the document, so
 * reading order and visual order agree at every width.
 *
 * The map's cell is `minmax(0, 1fr)`, whose minimum is zero rather than `auto`: the map
 * scales to the width it is given and never asks for more, so the grid cannot be pushed
 * wider than its container at any viewport.
 */

import JurisdictionMap from '@/components/JurisdictionMap'
import LatestCasesSidebar from '@/components/LatestCasesSidebar'
import SearchBar from '@/components/SearchBar'
import SubscribeForm from '@/components/SubscribeForm'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

export default function Home(): JSX.Element {
  useDocumentTitle('')

  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <h1 className="text-plt-accent-deep font-display text-3xl font-bold sm:text-4xl">
          Pesticide Litigation Tracker (PLT)
        </h1>
        <p className="text-plt-muted max-w-prose text-lg leading-relaxed">
          An open-access, automatically updated database of pesticide-related case law from the
          European Union and its member states, by Wageningen Law.
        </p>
        <SearchBar />
      </div>

      <div className="grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <JurisdictionMap className="min-h-[22rem]" />
        <div className="space-y-8">
          <LatestCasesSidebar />
          <SubscribeForm />
        </div>
      </div>
    </div>
  )
}
