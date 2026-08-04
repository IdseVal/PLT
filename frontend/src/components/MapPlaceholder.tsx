/**
 * The map slot on the home page, until the map itself is built (issue #13).
 *
 * This component exists to hold the position the map occupies — directly below the search
 * bar, filling the main column beside the latest-cases sidebar (`docs/architecture.md`
 * section 6) — so that the page can be laid out, measured and reviewed before the map lands.
 *
 * ## Replacing it
 *
 * Issue #13 adds `src/components/JurisdictionMap.tsx` and swaps the single element in
 * `src/pages/Home.tsx`:
 *
 * ```tsx
 * <MapPlaceholder />   ->   <JurisdictionMap />
 * ```
 *
 * then deletes this file and its test. The slot asks nothing of the map beyond an optional
 * `className`, which `Home` uses to size the slot; the map reads
 * `GET /api/stats/jurisdictions` itself, through `src/api/client.ts`, so no data is threaded
 * through the home page.
 */

/** Properties of {@link MapPlaceholder}. */
export interface MapPlaceholderProps {
  /** Extra classes for the slot, so the page owns the size and the component owns the look. */
  readonly className?: string
}

/**
 * Render the reserved map area.
 *
 * @param props - Component properties.
 * @returns The placeholder slot.
 */
export default function MapPlaceholder({ className = '' }: MapPlaceholderProps): JSX.Element {
  return (
    <section
      aria-labelledby="map-slot-heading"
      className={`border-plt-border bg-plt-accent-soft flex flex-col items-center justify-center gap-3 rounded border border-dashed p-8 text-center ${className}`}
    >
      <h2 id="map-slot-heading" className="text-plt-accent-deep font-display text-xl font-bold">
        Map of jurisdictions
      </h2>
      <p className="text-plt-muted max-w-prose text-sm leading-relaxed">
        A map of Europe will stand here, with one shape per jurisdiction showing how many cases
        the tracker holds for it, and the European Union alongside them as a jurisdiction in its
        own right.
      </p>
      <p className="text-plt-muted max-w-prose text-sm leading-relaxed">
        It arrives in a coming release. Until then, every case is reachable through the search
        above and the listing beside it.
      </p>
    </section>
  )
}
