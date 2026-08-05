/**
 * Home-page sidebar: the twenty most recent cases, and a way through to the full listing.
 *
 * Reads `GET /api/cases/latest?limit=20` through `src/api/client.ts` — no component in this
 * file calls `fetch` (`docs/architecture.md` section 6).
 *
 * All four states are designed rather than left to chance: while loading it shows the shape
 * of the list it is about to show, an empty collection is explained in words, and a failed
 * request leaves the rest of the page intact, says what went wrong and offers a retry. Case
 * titles and court names are **untrusted court text** and are rendered as text; nothing here
 * goes near `dangerouslySetInnerHTML`.
 */

import { Link } from 'react-router-dom'

import { LATEST_CASES_DEFAULT_LIMIT } from '@/api/client'
import type { ApiError } from '@/api/client'
import { useLatestCases } from '@/hooks/useLatestCases'
import type { CaseSummary } from '@/types/api'
import { caseLabel, cleanInlineText } from '@/utils/caseText'
import { formatDecisionDate } from '@/utils/dates'
import { caseDetailPath } from '@/utils/links'

/** Skeleton rows shown while the feed loads: enough to suggest a list, not a full page of them. */
const SKELETON_ROWS = 5

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
      return 'Loading the latest cases took too long.'
    case 'invalid_response':
      return 'The case database answered in a form this page could not read.'
    default:
      return error.message
  }
}

/** Properties of {@link CaseRow}. */
interface CaseRowProps {
  /** The case to render. */
  readonly item: CaseSummary
}

/**
 * One case in the feed: its title as a link, with the metadata that identifies it.
 *
 * The label comes from `caseLabel`, so a case the source published no title for is listed
 * under its ECLI or CELEX rather than as a nameless link. Every value here is untrusted court
 * text and goes through `src/utils/caseText.ts` before it is rendered as a React child.
 *
 * @param props - Component properties.
 * @returns The list row.
 */
function CaseRow({ item }: CaseRowProps): JSX.Element {
  const title = caseLabel(item.title, item.source_id)
  const jurisdiction = cleanInlineText(item.jurisdiction_name) || cleanInlineText(item.jurisdiction_code)
  const court = cleanInlineText(item.court_name)
  const decisionDate = formatDecisionDate(item.decision_date)

  return (
    <li className="border-plt-border border-b py-3 first:pt-0 last:border-b-0 last:pb-0">
      <Link
        to={caseDetailPath(item.jurisdiction_code, item.source_id)}
        className="text-plt-accent-strong rounded-sm text-sm font-semibold leading-snug underline underline-offset-2"
      >
        {title}
      </Link>
      <p className="text-plt-muted mt-1 text-xs leading-relaxed">
        <span className="sr-only">Jurisdiction: </span>
        <span>{jurisdiction}</span>
        {court !== '' && (
          <>
            <span aria-hidden="true"> · </span>
            <span className="sr-only">Court: </span>
            <span>{court}</span>
          </>
        )}
        {decisionDate !== null && item.decision_date !== null && (
          <>
            <span aria-hidden="true"> · </span>
            <span className="sr-only">Decided: </span>
            <time dateTime={item.decision_date}>{decisionDate}</time>
          </>
        )}
      </p>
    </li>
  )
}

/**
 * Render the loading state: the outline of the list, not a bare spinner.
 *
 * @returns The skeleton.
 */
function LoadingState(): JSX.Element {
  return (
    <div role="status" aria-live="polite" className="space-y-4">
      <span className="sr-only">Loading the latest cases…</span>
      {Array.from({ length: SKELETON_ROWS }, (_unused, index) => (
        <div key={index} aria-hidden="true" className="animate-pulse space-y-2 py-1">
          <div className="bg-plt-accent-soft h-3 w-full rounded" />
          <div className="bg-plt-accent-soft h-3 w-4/5 rounded" />
          <div className="bg-plt-accent-soft h-2 w-1/2 rounded" />
        </div>
      ))}
    </div>
  )
}

/** Properties of {@link LatestCasesSidebar}. */
export interface LatestCasesSidebarProps {
  /** How many cases to show. The contract puts twenty in the sidebar. */
  readonly limit?: number
}

/**
 * Render the latest-cases sidebar.
 *
 * @param props - Component properties.
 * @returns The sidebar.
 */
export default function LatestCasesSidebar({
  limit = LATEST_CASES_DEFAULT_LIMIT,
}: LatestCasesSidebarProps): JSX.Element {
  const { state, reload } = useLatestCases(limit)

  return (
    <aside
      aria-labelledby="latest-cases-heading"
      className="border-plt-border bg-plt-panel space-y-4 rounded border p-5"
    >
      <div className="space-y-1">
        <h2 id="latest-cases-heading" className="text-plt-accent-deep font-display text-xl font-bold">
          Latest cases
        </h2>
        <p className="text-plt-muted text-sm leading-relaxed">
          The most recent decisions added to the tracker, newest first.
        </p>
      </div>

      {state.status === 'loading' && <LoadingState />}

      {state.status === 'error' && (
        <div role="alert" className="border-plt-border bg-plt-accent-soft space-y-3 rounded border p-4">
          <p className="text-plt-ink text-sm font-semibold">The latest cases could not be loaded.</p>
          <p className="text-plt-muted text-sm leading-relaxed">{errorMessage(state.error)}</p>
          <button
            type="button"
            onClick={reload}
            className="border-plt-accent-strong text-plt-accent-strong rounded border px-3 py-1.5 text-sm font-semibold"
          >
            Try again
          </button>
        </div>
      )}

      {state.status === 'ready' && state.cases.length === 0 && (
        <p className="text-plt-muted text-sm leading-relaxed">
          No cases have been published yet. The collection fills as the weekly scan of the
          official sources runs; the{' '}
          <Link className="text-plt-accent-strong rounded-sm underline underline-offset-4" to="/methodology">
            methodology
          </Link>{' '}
          describes how cases are selected.
        </p>
      )}

      {state.status === 'ready' && state.cases.length > 0 && (
        <ol className="m-0 list-none p-0">
          {state.cases.map((item) => (
            <CaseRow key={`${item.jurisdiction_code}/${item.source_id}`} item={item} />
          ))}
        </ol>
      )}

      <Link
        to="/cases"
        className="bg-plt-accent-deep text-plt-inverse block rounded px-4 py-2.5 text-center text-sm font-semibold"
      >
        All cases
      </Link>
    </aside>
  )
}
