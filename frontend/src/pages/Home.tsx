/**
 * Home page.
 *
 * The composition is fixed by `docs/architecture.md` section 6 and core document section
 * 3.3: the title, a prominent search bar directly beneath it, the map below the search bar,
 * and a right-hand sidebar of the twenty most recent cases with a button through to the
 * all-cases page.
 *
 * The map itself is issue #13. Its position is held by `MapPlaceholder`, which that issue
 * replaces with `JurisdictionMap`; see the note in that file.
 *
 * On narrow viewports the two-column grid collapses to one column, so the sidebar moves
 * below the main column rather than disappearing. It follows the map in the document, so
 * reading order and visual order agree at every width.
 */

import LatestCasesSidebar from '@/components/LatestCasesSidebar'
import MapPlaceholder from '@/components/MapPlaceholder'
import SearchBar from '@/components/SearchBar'
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
        <MapPlaceholder className="min-h-[22rem]" />
        <LatestCasesSidebar />
      </div>
    </div>
  )
}
