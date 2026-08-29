/**
 * The per-jurisdiction keyword index on the methodology page.
 *
 * One `<details>` disclosure per jurisdiction which, when opened, lists every term on that
 * jurisdiction's curated keyword list, grouped by category and carrying the number of
 * published cases the term currently labels. The data is `GET /api/filters` — the same
 * payload the All-cases filter controls read — fetched through `src/api/client.ts`
 * (`docs/architecture.md` section 6), so the page always shows the lists the pipeline
 * actually applies rather than a copy that could drift.
 *
 * The terms travel over the wire, so they are treated as server-supplied text and rendered
 * through `src/utils/caseText.ts` like everything else that does. A native disclosure is
 * used rather than a scripted one because it is keyboard-operable and announced by screen
 * readers without any code here having to get that right.
 */

import { useCallback, useState } from 'react'

import { getFilters } from '@/api/client'
import type { ApiError } from '@/api/client'
import { useApiResource } from '@/hooks/useApiResource'
import type { FilterFacets, KeywordOption } from '@/types/api'
import { categoryLabel, cleanInlineText } from '@/utils/caseText'

/** The terms of one category within one jurisdiction, ready to render. */
interface CategoryGroup {
  /** The stored category value, used as the React key. */
  readonly category: string
  /** The category as shown to the reader, e.g. `Active substance`. */
  readonly label: string
  /** The category's terms, sorted alphabetically. */
  readonly terms: readonly KeywordOption[]
}

/** One jurisdiction's slice of the index. */
interface JurisdictionGroup {
  /** Jurisdiction code, e.g. `NL`. */
  readonly code: string
  /** Human-readable jurisdiction name, falling back to the code. */
  readonly name: string
  /** Total terms on this jurisdiction's list, for the disclosure summary. */
  readonly termCount: number
  readonly categories: readonly CategoryGroup[]
}

/**
 * Group the facet payload's keywords by jurisdiction and, within each, by category.
 *
 * Jurisdictions keep the order of the payload's own `jurisdictions` array — the order the
 * rest of the site lists them in — with any jurisdiction that appears only in the keyword
 * data appended after it, so a list published ahead of its cases is still shown. Categories
 * and terms are sorted alphabetically: the lists run to hundreds of terms, and alphabetical
 * order is the only one a reader can predict.
 *
 * @param facets - The `GET /api/filters` payload.
 * @returns The index, one entry per jurisdiction that has any terms.
 */
function groupKeywords(facets: FilterFacets): readonly JurisdictionGroup[] {
  const names = new Map(facets.jurisdictions.map((entry) => [entry.code, entry.name]))

  const byJurisdiction = new Map<string, Map<string, KeywordOption[]>>()
  for (const option of facets.keywords ?? []) {
    const categories = byJurisdiction.get(option.jurisdiction) ?? new Map<string, KeywordOption[]>()
    const terms = categories.get(option.category) ?? []
    terms.push(option)
    categories.set(option.category, terms)
    byJurisdiction.set(option.jurisdiction, categories)
  }

  const orderedCodes = [
    ...facets.jurisdictions.map((entry) => entry.code).filter((code) => byJurisdiction.has(code)),
    ...[...byJurisdiction.keys()].filter((code) => !names.has(code)).sort(),
  ]

  return orderedCodes.map((code) => {
    const categories = [...(byJurisdiction.get(code) ?? new Map<string, KeywordOption[]>())]
      .map(([category, terms]) => ({
        category,
        label: categoryLabel(cleanInlineText(category)),
        terms: [...terms].sort((a, b) => a.term.localeCompare(b.term)),
      }))
      .sort((a, b) => a.label.localeCompare(b.label))

    return {
      code,
      name: cleanInlineText(names.get(code)) || cleanInlineText(code),
      termCount: categories.reduce((total, group) => total + group.terms.length, 0),
      categories,
    }
  })
}

/**
 * Message for a failed request.
 *
 * The API's own message is shown when it sent one, because it is written for a reader
 * (`ApiErrorEnvelope`). Failures that never reached the API have no such message, so this
 * supplies one instead of surfacing an internal string.
 *
 * @param error - The failure.
 * @returns A sentence to show the reader.
 */
function errorMessage(error: ApiError): string {
  switch (error.code) {
    case 'network_error':
      return 'The tracker could not reach the case database. It may be offline for maintenance.'
    case 'request_aborted':
      return 'Loading the keyword lists took too long.'
    default:
      return error.message
  }
}

/**
 * One jurisdiction's disclosure: a summary naming the list, and the grouped terms inside.
 *
 * @param props - Component properties.
 * @param props.group - The jurisdiction to render.
 * @returns The disclosure.
 */
function JurisdictionDisclosure({ group }: { readonly group: JurisdictionGroup }): JSX.Element {
  return (
    <details className="border-plt-border bg-plt-panel max-w-prose rounded border">
      <summary className="text-plt-accent-strong cursor-pointer rounded px-4 py-3 font-semibold">
        {group.name}
        <span className="text-plt-muted font-normal">
          {' — '}
          {String(group.termCount)} {group.termCount === 1 ? 'term' : 'terms'}
        </span>
      </summary>
      <div className="border-plt-border space-y-5 border-t px-4 py-4">
        {group.categories.map((category) => (
          <div key={category.category} className="space-y-2">
            <h3 className="text-plt-ink text-sm font-semibold uppercase tracking-wide">
              {category.label}
            </h3>
            <ul className="text-plt-ink columns-1 text-sm leading-relaxed sm:columns-2">
              {category.terms.map((option) => (
                <li key={option.id} className="break-inside-avoid py-0.5">
                  {cleanInlineText(option.term)}
                  <span className="text-plt-muted">
                    {' '}
                    ({String(option.case_count)} {option.case_count === 1 ? 'case' : 'cases'})
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </details>
  )
}

/** Properties of {@link KeywordIndexLoader}. */
interface KeywordIndexLoaderProps {
  /** Called by the retry control on the error state. */
  readonly onRetry: () => void
}

/**
 * Fetch the facets and render whichever of the three states applies.
 *
 * @param props - Component properties.
 * @returns The loading indicator, the error state, or the index.
 */
function KeywordIndexLoader({ onRetry }: KeywordIndexLoaderProps): JSX.Element {
  const load = useCallback((signal: AbortSignal) => getFilters(signal), [])
  const { data, error, isLoading } = useApiResource(load)

  if (isLoading && data === null) {
    return (
      <p role="status" className="text-plt-muted max-w-prose leading-relaxed">
        Loading the keyword lists…
      </p>
    )
  }

  if (error !== null && data === null) {
    return (
      <div role="alert" className="border-plt-border bg-plt-accent-soft max-w-prose space-y-3 rounded border p-4">
        <p className="text-plt-ink text-sm font-semibold">The keyword lists could not be loaded.</p>
        <p className="text-plt-muted text-sm leading-relaxed">{errorMessage(error)}</p>
        <button
          type="button"
          onClick={onRetry}
          className="border-plt-accent-strong text-plt-accent-strong rounded border px-3 py-1.5 text-sm font-semibold"
        >
          Try again
        </button>
      </div>
    )
  }

  const groups = data === null ? [] : groupKeywords(data)

  if (groups.length === 0) {
    return (
      <p className="text-plt-muted max-w-prose leading-relaxed">
        The keyword lists are not available right now. They can be read in the project
        repository, under data/keywords.
      </p>
    )
  }

  return (
    <div className="space-y-4">
      {groups.map((group) => (
        <JurisdictionDisclosure key={group.code} group={group} />
      ))}
    </div>
  )
}

/**
 * Render the per-jurisdiction keyword index, fetching it from the API.
 *
 * A retry does not patch state in place: it remounts {@link KeywordIndexLoader} through a
 * `key` change, which restarts the fetch with the loader untouched and keeps
 * `useApiResource` the only owner of request state.
 *
 * @returns The index, in whichever state the request is in.
 */
export default function KeywordIndex(): JSX.Element {
  const [attempt, setAttempt] = useState(0)

  return (
    <KeywordIndexLoader
      key={attempt}
      onRetry={() => {
        setAttempt((previous) => previous + 1)
      }}
    />
  )
}
