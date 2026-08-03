/**
 * All cases: the full listing, with the section 2.2 classification filters, pagination and
 * download.
 *
 * The query string is the state of this page. Filters, sort, page and page size are read
 * from the URL on every render and written back to it on every change, so a filtered view is
 * a link: it can be shared with a colleague, bookmarked, opened in a second tab and cited in
 * a paper, and it survives a reload unchanged. Nothing about the selection is kept in
 * component state that the URL does not also carry.
 *
 * All server data comes through `src/api/client.ts` (`docs/architecture.md` section 6), and
 * every string that reaches the page from a case is rendered as text — see
 * `src/utils/caseText.ts` for why that is not negotiable.
 */

import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'

import { getFilters, searchCases } from '@/api/client'
import CaseCard from '@/components/cases/CaseCard'
import CaseFilters from '@/components/cases/CaseFilters'
import ExportLinks from '@/components/cases/ExportLinks'
import Pagination from '@/components/cases/Pagination'
import { BUTTON_SECONDARY, SELECT } from '@/components/cases/controls'
import { useApiResource } from '@/hooks/useApiResource'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import {
  activeFilterCount,
  caseFiltersToSearchParams,
  pageCount,
  parseCaseFilters,
  toSearchQuery,
  withFilterChanges,
  DEFAULT_PAGE_SIZE,
  PAGE_SIZE_OPTIONS,
  SORT_OPTIONS,
  type CaseFilterState,
} from '@/utils/caseFilters'
import { formatCount } from '@/utils/dates'
import type { CaseSort } from '@/types/api'

/**
 * A sentence stating how many cases the current selection holds.
 *
 * @param total - Number of matching cases.
 * @param filtered - Whether any filter is narrowing the collection.
 * @returns The sentence to announce.
 */
function resultSummary(total: number, filtered: boolean): string {
  if (total === 0) return 'No cases match these filters.'
  const noun = total === 1 ? 'case' : 'cases'
  return filtered
    ? `${formatCount(total)} ${noun} match these filters.`
    : `${formatCount(total)} ${noun} in the collection.`
}

/**
 * The All-cases page.
 *
 * @returns The listing.
 */
export default function AllCases(): JSX.Element {
  useDocumentTitle('All cases')

  const [searchParams, setSearchParams] = useSearchParams()
  const filters = useMemo(() => parseCaseFilters(searchParams), [searchParams])
  const filterCount = activeFilterCount(filters)

  const loadCases = useCallback(
    (signal: AbortSignal) => searchCases(toSearchQuery(filters), signal),
    [filters],
  )
  const loadFacets = useCallback((signal: AbortSignal) => getFilters(signal), [])

  const results = useApiResource(loadCases)
  const facets = useApiResource(loadFacets)

  const summaryRef = useRef<HTMLParagraphElement>(null)
  const isFirstRender = useRef(true)

  // A single-page application changes the page under the reader without moving focus, which
  // leaves a keyboard or screen-reader user at the top of the document with no sign that the
  // results changed. Focus follows the selection instead, landing on the count.
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false
      return
    }
    summaryRef.current?.focus()
  }, [filters])

  /**
   * Write a change to the selection into the URL.
   *
   * @param patch - Fields to change. Anything but a page change returns to page 1.
   */
  const update = useCallback(
    (patch: Partial<CaseFilterState>): void => {
      setSearchParams(caseFiltersToSearchParams(withFilterChanges(filters, patch)))
    },
    [filters, setSearchParams],
  )

  /**
   * The URL of another page of the same selection.
   *
   * @param page - One-based page number.
   * @returns A path the paginator can link to.
   */
  const hrefForPage = useCallback(
    (page: number): string => `/cases?${caseFiltersToSearchParams({ ...filters, page }).toString()}`,
    [filters],
  )

  const result = results.data
  const total = result?.total ?? null
  const totalPages = result === null ? 1 : pageCount(result.total, result.page_size)
  const items = result?.items ?? []

  return (
    <section className="space-y-6">
      <header className="space-y-3">
        <h1 className="text-plt-accent-deep font-display text-3xl font-bold sm:text-4xl">
          All cases
        </h1>
        <p className="text-plt-muted max-w-prose leading-relaxed">
          Every judgment in the tracker, filtered by the classification the collection is built
          on. The address bar holds the whole selection, so a filtered list can be shared and
          cited exactly as it appears here.
        </p>
      </header>

      <div className="grid gap-8 lg:grid-cols-4">
        <div className="lg:col-span-1">
          <CaseFilters
            filters={filters}
            facets={facets.data}
            facetsFailed={facets.error !== null}
            onApply={update}
            onClear={() => {
              setSearchParams(new URLSearchParams())
            }}
          />
        </div>

        <div className="lg:col-span-3">
          <div className="border-plt-border flex flex-wrap items-end justify-between gap-4 border-b pb-4">
            <p
              ref={summaryRef}
              tabIndex={-1}
              role="status"
              aria-live="polite"
              className="text-plt-ink text-base font-medium"
            >
              {results.error !== null
                ? 'The results could not be loaded.'
                : result === null
                  ? 'Searching…'
                  : resultSummary(result.total, filterCount > 0)}
            </p>

            <div className="flex flex-wrap items-end gap-4">
              <div className="space-y-1">
                <label className="text-plt-muted block text-sm" htmlFor="cases-sort">
                  Sort by
                </label>
                <select
                  id="cases-sort"
                  className={`${SELECT} w-auto`}
                  value={filters.sort}
                  onChange={(event) => {
                    update({ sort: event.target.value as CaseSort })
                  }}
                >
                  {SORT_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-1">
                <label className="text-plt-muted block text-sm" htmlFor="cases-page-size">
                  Per page
                </label>
                <select
                  id="cases-page-size"
                  className={`${SELECT} w-auto`}
                  value={filters.page_size}
                  onChange={(event) => {
                    update({
                      page_size: Number.parseInt(event.target.value, 10) || DEFAULT_PAGE_SIZE,
                    })
                  }}
                >
                  {PAGE_SIZE_OPTIONS.map((size) => (
                    <option key={size} value={size}>
                      {size}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          <div className="mt-4">
            <ExportLinks filters={filters} total={total} />
          </div>

          {results.error === null ? null : (
            <p role="alert" className="border-plt-border text-plt-ink mt-6 border-l-4 pl-4 leading-relaxed">
              {results.error.isNetworkError
                ? 'The tracker could not be reached. Check your connection and try again.'
                : results.error.message}
            </p>
          )}

          <div aria-busy={results.isLoading} className="mt-6">
            {result !== null && result.total === 0 ? (
              <div className="border-plt-border bg-plt-panel rounded-sm border p-6">
                <h2 className="text-plt-accent-deep font-display text-lg font-bold">
                  Nothing matched
                </h2>
                <p className="text-plt-muted mt-2 max-w-prose leading-relaxed">
                  No case in the tracker matches this combination. Widening the date range or
                  removing a filter is usually enough; the collection is still growing, and a
                  jurisdiction is only present once its keyword list has been written.
                </p>
                {filterCount === 0 ? null : (
                  <p className="mt-4">
                    <button
                      type="button"
                      className={BUTTON_SECONDARY}
                      onClick={() => {
                        setSearchParams(new URLSearchParams())
                      }}
                    >
                      Clear all filters
                    </button>
                  </p>
                )}
              </div>
            ) : (
              <ol className="space-y-4">
                {items.map((item) => (
                  <li key={`${item.jurisdiction_code}/${item.source_id}`}>
                    <CaseCard item={item} />
                  </li>
                ))}
              </ol>
            )}
          </div>

          {result === null ? null : (
            <Pagination page={result.page} pageCount={totalPages} hrefForPage={hrefForPage} />
          )}
        </div>
      </div>
    </section>
  )
}
