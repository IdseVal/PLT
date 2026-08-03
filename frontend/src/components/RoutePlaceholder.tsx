/**
 * Placeholder for a route whose page is built by another issue.
 *
 * The shell (this issue) owns the route table, but not the pages behind `/cases` and
 * `/cases/:jurisdiction/:sourceId`. Those routes render this component so that navigation,
 * document titles and the layout can be exercised before the real pages land, and so that a
 * reviewer can see at a glance which parts of the site are not built yet. The issue that
 * owns a page replaces its placeholder outright.
 */

import { useDocumentTitle } from '@/hooks/useDocumentTitle'

/** Properties of {@link RoutePlaceholder}. */
export interface RoutePlaceholderProps {
  /** Level-1 heading for the route. */
  readonly title: string
  /** One line describing what will be here, in the language of the site rather than of the tracker. */
  readonly description: string
}

/**
 * Render a minimal page for a route that is not yet implemented.
 *
 * @param props - Component properties.
 * @returns The placeholder page.
 */
export default function RoutePlaceholder({ title, description }: RoutePlaceholderProps): JSX.Element {
  useDocumentTitle(title)

  return (
    <section className="space-y-4">
      <h1 className="text-plt-accent-deep font-display text-3xl font-bold sm:text-4xl">{title}</h1>
      <p className="text-plt-muted max-w-prose leading-relaxed">{description}</p>
      <p className="border-plt-border text-plt-muted max-w-prose border-l-4 pl-4 text-sm">
        This part of the tracker is still being built and will arrive in a coming release.
      </p>
    </section>
  )
}
