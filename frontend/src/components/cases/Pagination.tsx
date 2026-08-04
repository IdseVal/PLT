/**
 * Pagination for the All-cases listing.
 *
 * Every destination is a real link, not a button: a page of a filtered result set has its
 * own URL, so it can be opened in a new tab, bookmarked and cited. The component is given a
 * function that turns a page number into that URL rather than building one itself, because
 * the query string is assembled in one place (`src/utils/caseFilters.ts`).
 *
 * Only a window of page numbers is rendered — a 400-page result set would otherwise put 400
 * links in the tab order — with the first and last page always reachable and an ellipsis
 * where numbers are skipped.
 */

import { Link } from 'react-router-dom'

import { formatCount } from '@/utils/dates'

/** Properties of {@link Pagination}. */
export interface PaginationProps {
  /** One-based current page. */
  readonly page: number
  /** Total number of pages, at least 1. */
  readonly pageCount: number
  /** Build the URL for a page. */
  readonly hrefForPage: (page: number) => string
}

/** Page numbers either side of the current page that are always shown. */
const WINDOW = 2

/** Marker for a gap in the page-number sequence. */
const GAP = 'gap'

/**
 * The page numbers to render, with gaps marked.
 *
 * @param page - Current page.
 * @param pageCount - Total pages.
 * @returns Page numbers and gap markers, in order.
 */
function pageItems(page: number, pageCount: number): (number | typeof GAP)[] {
  const shown = new Set<number>([1, pageCount])
  for (let candidate = page - WINDOW; candidate <= page + WINDOW; candidate += 1) {
    if (candidate >= 1 && candidate <= pageCount) shown.add(candidate)
  }

  const ordered = [...shown].sort((left, right) => left - right)
  const items: (number | typeof GAP)[] = []
  let previous = 0
  for (const value of ordered) {
    if (previous !== 0 && value - previous > 1) items.push(GAP)
    items.push(value)
    previous = value
  }
  return items
}

/** Classes shared by every page control. */
const CONTROL = 'inline-flex items-center justify-center rounded-sm border px-3 py-2 text-sm'

/**
 * Render the paginator.
 *
 * @param props - Component properties.
 * @returns The navigation landmark, or `null` when there is only one page.
 */
export default function Pagination({ page, pageCount, hrefForPage }: PaginationProps): JSX.Element | null {
  if (pageCount <= 1) return null

  const isFirst = page <= 1
  const isLast = page >= pageCount

  return (
    <nav aria-label="Pagination" className="border-plt-border mt-8 border-t pt-6">
      <ul className="flex flex-wrap items-center justify-center gap-2">
        <li>
          {isFirst ? (
            // The control stays in place when it is unavailable, so the row does not
            // reflow between pages. A disabled control is not a link, so it is not focusable.
            <span
              className={`${CONTROL} border-plt-border text-plt-muted`}
              aria-disabled="true"
            >
              Previous
            </span>
          ) : (
            <Link
              className={`${CONTROL} border-plt-border text-plt-accent-strong font-medium`}
              to={hrefForPage(page - 1)}
              rel="prev"
            >
              Previous
            </Link>
          )}
        </li>

        {pageItems(page, pageCount).map((item, index) =>
          item === GAP ? (
            <li key={`gap-${String(index)}`} aria-hidden="true" className="text-plt-muted px-1">
              &hellip;
            </li>
          ) : (
            <li key={item}>
              {item === page ? (
                <span
                  aria-current="page"
                  className={`${CONTROL} border-plt-accent-deep bg-plt-accent-deep text-plt-inverse font-semibold`}
                >
                  <span className="sr-only">Page </span>
                  {formatCount(item)}
                </span>
              ) : (
                <Link
                  className={`${CONTROL} border-plt-border text-plt-accent-strong`}
                  to={hrefForPage(item)}
                >
                  <span className="sr-only">Page </span>
                  {formatCount(item)}
                </Link>
              )}
            </li>
          ),
        )}

        <li>
          {isLast ? (
            <span className={`${CONTROL} border-plt-border text-plt-muted`} aria-disabled="true">
              Next
            </span>
          ) : (
            <Link
              className={`${CONTROL} border-plt-border text-plt-accent-strong font-medium`}
              to={hrefForPage(page + 1)}
              rel="next"
            >
              Next
            </Link>
          )}
        </li>
      </ul>

      <p className="text-plt-muted mt-3 text-center text-sm">
        Page {formatCount(page)} of {formatCount(pageCount)}
      </p>
    </nav>
  )
}
