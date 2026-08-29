/**
 * The exclusion criteria on the methodology page, each under the mechanism it belongs to.
 *
 * The methodology page describes three mechanisms in a definition list, and each definition
 * carries a disclosure listing exactly what that mechanism keeps out in each jurisdiction.
 * The data is `GET /api/exclusions`, read from the curated lists and their record of rejected
 * terms rather than from the cases, so the page shows the criterion as it stands rather than
 * as some past run applied it.
 *
 * The three are kept apart rather than merged into one list because they are not the same
 * claim. A term left off the list never runs at all; a gated term runs but cannot admit a
 * case on its own; an exclusion pattern rejects a document that other terms had already
 * matched. A reader auditing the method needs to know which of those applied.
 *
 * This renders the whole definition list rather than one disclosure, so the page pays for a
 * single request however many mechanisms it describes, and a failure is reported once instead
 * of three times.
 *
 * Everything from the API is server-supplied text and is rendered through
 * `src/utils/caseText.ts` like the rest of it. Native disclosures are used rather than
 * scripted ones: they are keyboard-operable and announced by screen readers without any code
 * here having to get that right, which is why `KeywordIndex` uses them too.
 */

import { useCallback, useState } from 'react'

import { getExclusions } from '@/api/client'
import type { ApiError } from '@/api/client'
import { useApiResource } from '@/hooks/useApiResource'
import type { ExclusionsResponse, JurisdictionExclusions } from '@/types/api'
import type { ContentDefinition, ExclusionMechanism } from '@/types/content'
import { categoryLabel, cleanInlineText } from '@/utils/caseText'

/** One row of a mechanism: what is excluded, and why. */
interface ExclusionRow {
  /** React key, unique within its jurisdiction. */
  readonly key: string
  /** The term or phrase itself. */
  readonly subject: string
  /** A qualifier shown beside the subject: a category, or the gate that opens the term. */
  readonly qualifier: string
  /** The recorded reason, where the mechanism records one. */
  readonly reason: string
}

/** One jurisdiction's rows under one mechanism. */
interface MechanismJurisdiction {
  readonly code: string
  readonly name: string
  readonly rows: readonly ExclusionRow[]
}

/** How one mechanism reads: where its rows come from, and what to call them. */
interface MechanismSpec {
  /** Reads as "Show all 34 rejected terms". Pluralised by {@link MechanismSpec.one}. */
  readonly many: string
  /** Singular of {@link MechanismSpec.many}, for a mechanism with one entry. */
  readonly one: string
  /** How to introduce {@link ExclusionRow.qualifier}; empty when it speaks for itself. */
  readonly qualifierLabel: string
  /** What a jurisdiction with nothing under this mechanism should say. */
  readonly emptyLabel: string
  readonly rowsOf: (entry: JurisdictionExclusions) => readonly ExclusionRow[]
}

/** The three mechanisms the API reports, keyed by the value a definition names. */
const MECHANISMS: Record<ExclusionMechanism, MechanismSpec> = {
  'left-off': {
    many: 'rejected terms',
    one: 'rejected term',
    qualifierLabel: '',
    emptyLabel: 'No term has been rejected for this jurisdiction yet.',
    rowsOf: (entry) =>
      (entry.excluded_terms ?? []).map((term) => ({
        key: term.id || term.term,
        subject: cleanInlineText(term.term),
        qualifier: categoryLabel(cleanInlineText(term.category)),
        reason: cleanInlineText(term.reason),
      })),
  },
  gated: {
    many: 'gated terms',
    one: 'gated term',
    qualifierLabel: 'only counts alongside',
    emptyLabel: 'No term is gated for this jurisdiction.',
    rowsOf: (entry) =>
      (entry.gated_terms ?? []).map((term) => ({
        key: term.id || term.term,
        subject: cleanInlineText(term.term),
        qualifier: (term.requires ?? []).map((gate) => cleanInlineText(gate)).join(', '),
        reason: '',
      })),
  },
  patterns: {
    many: 'exclusion patterns',
    one: 'exclusion pattern',
    qualifierLabel: '',
    emptyLabel: 'No exclusion pattern is applied in this jurisdiction.',
    rowsOf: (entry) =>
      (entry.exclusion_patterns ?? []).map((pattern) => ({
        key: pattern.pattern,
        subject: cleanInlineText(pattern.pattern),
        qualifier: '',
        reason: cleanInlineText(pattern.reason),
      })),
  },
}

/**
 * Group one mechanism's entries by jurisdiction.
 *
 * Jurisdictions keep the payload's order, which is the order the rest of the site lists them
 * in. Rows are sorted by subject: these run to dozens of entries, and alphabetical order is
 * the only one a reader can predict.
 *
 * @param payload - The `GET /api/exclusions` response.
 * @param mechanism - Which mechanism to collect.
 * @returns One entry per jurisdiction, in payload order.
 */
function groupByJurisdiction(
  payload: ExclusionsResponse,
  mechanism: ExclusionMechanism,
): readonly MechanismJurisdiction[] {
  const { rowsOf } = MECHANISMS[mechanism]
  return (payload.jurisdictions ?? []).map((entry) => ({
    code: entry.code,
    name: cleanInlineText(entry.name) || cleanInlineText(entry.code),
    rows: [...rowsOf(entry)].sort((a, b) => a.subject.localeCompare(b.subject)),
  }))
}

/**
 * Message for a failed request.
 *
 * The API's own message is shown when it sent one, because it is written for a reader.
 * Failures that never reached the API have none, so this supplies one rather than surfacing
 * an internal string.
 *
 * @param error - The failure.
 * @returns A sentence to show the reader.
 */
function errorMessage(error: ApiError): string {
  switch (error.code) {
    case 'network_error':
      return 'The tracker could not reach the case database. It may be offline for maintenance.'
    case 'request_aborted':
      return 'Loading the exclusion criteria took too long.'
    default:
      return error.message
  }
}

/** Properties of {@link MechanismDisclosure}. */
interface MechanismDisclosureProps {
  readonly mechanism: ExclusionMechanism
  readonly payload: ExclusionsResponse
}

/**
 * One mechanism's disclosure, sitting under the definition that describes it.
 *
 * @param props - Component properties.
 * @returns The disclosure.
 */
function MechanismDisclosure({ mechanism, payload }: MechanismDisclosureProps): JSX.Element {
  const spec = MECHANISMS[mechanism]
  const jurisdictions = groupByJurisdiction(payload, mechanism)
  const total = jurisdictions.reduce((sum, entry) => sum + entry.rows.length, 0)

  return (
    <details className="border-plt-border bg-plt-panel mt-3 rounded border">
      <summary className="text-plt-accent-strong cursor-pointer rounded px-4 py-2.5 text-sm font-semibold">
        {`Show all ${String(total)} ${total === 1 ? spec.one : spec.many}`}
      </summary>
      <div className="border-plt-border space-y-5 border-t px-4 py-4">
        {jurisdictions.map((entry) => (
          <div key={entry.code} className="space-y-2">
            <h3 className="text-plt-ink text-sm font-semibold uppercase tracking-wide">
              {entry.name}
            </h3>
            {entry.rows.length === 0 ? (
              <p className="text-plt-muted text-sm">{spec.emptyLabel}</p>
            ) : (
              <ul className="text-plt-ink space-y-2 text-sm leading-relaxed">
                {entry.rows.map((row) => (
                  <li key={row.key}>
                    <span className="font-medium">{row.subject}</span>
                    {row.qualifier === '' ? null : (
                      <span className="text-plt-muted">
                        {' — '}
                        {spec.qualifierLabel === ''
                          ? row.qualifier
                          : `${spec.qualifierLabel} ${row.qualifier}`}
                      </span>
                    )}
                    {row.reason === '' ? null : (
                      <span className="text-plt-muted block">{row.reason}</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </details>
  )
}

/** Properties of {@link ExclusionDefinitionsLoader} and {@link ExclusionDefinitions}. */
interface ExclusionDefinitionsProps {
  /** The definition items to render, in the order the page lists them. */
  readonly items: readonly ContentDefinition[]
}

/** Properties of {@link ExclusionDefinitionsLoader}. */
interface ExclusionDefinitionsLoaderProps extends ExclusionDefinitionsProps {
  /** Called by the retry control on the error state. */
  readonly onRetry: () => void
}

/**
 * Fetch the criteria once and render the definition list around them.
 *
 * @param props - Component properties.
 * @returns The definition list, each mechanism carrying its own disclosure.
 */
function ExclusionDefinitionsLoader({
  items,
  onRetry,
}: ExclusionDefinitionsLoaderProps): JSX.Element {
  const load = useCallback((signal: AbortSignal) => getExclusions(signal), [])
  const { data, error, isLoading } = useApiResource(load)
  const failed = error !== null && data === null

  return (
    <>
      <dl className="max-w-prose space-y-3">
        {items.map((item) => (
          <div key={item.term}>
            <dt className="text-plt-ink font-semibold">{item.term}</dt>
            <dd className="text-plt-ink leading-relaxed">
              {item.description}
              {item.mechanism === undefined ? null : (
                <>
                  {isLoading && data === null ? (
                    <p role="status" className="text-plt-muted mt-2 text-sm">
                      Loading the exclusion criteria…
                    </p>
                  ) : null}
                  {data === null ? null : (
                    <MechanismDisclosure mechanism={item.mechanism} payload={data} />
                  )}
                </>
              )}
            </dd>
          </div>
        ))}
      </dl>

      {failed ? (
        <div
          role="alert"
          className="border-plt-border bg-plt-accent-soft mt-4 max-w-prose space-y-3 rounded border p-4"
        >
          <p className="text-plt-ink text-sm font-semibold">
            The exclusion criteria could not be loaded.
          </p>
          <p className="text-plt-muted text-sm leading-relaxed">{errorMessage(error)}</p>
          <button
            type="button"
            onClick={onRetry}
            className="border-plt-accent-strong text-plt-accent-strong rounded border px-3 py-1.5 text-sm font-semibold"
          >
            Try again
          </button>
        </div>
      ) : null}
    </>
  )
}

/**
 * Render a definition list whose items carry exclusion disclosures.
 *
 * One request serves every mechanism on the page, so a definition list describing all three
 * costs one call and reports a failure once.
 *
 * A retry does not patch state in place: it remounts {@link ExclusionDefinitionsLoader}
 * through a `key` change, which restarts the fetch with the loader untouched and keeps
 * `useApiResource` the only owner of request state.
 *
 * @param props - Component properties.
 * @returns The definition list.
 */
export default function ExclusionDefinitions({ items }: ExclusionDefinitionsProps): JSX.Element {
  const [attempt, setAttempt] = useState(0)

  return (
    <ExclusionDefinitionsLoader
      key={attempt}
      items={items}
      onRetry={() => {
        setAttempt((previous) => previous + 1)
      }}
    />
  )
}
