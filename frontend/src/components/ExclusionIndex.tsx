/**
 * The exclusion criteria on the methodology page, mechanism by mechanism.
 *
 * Three `<details>` disclosures, one per mechanism, each listing exactly what that mechanism
 * keeps out in each jurisdiction. The data is `GET /api/exclusions`, read from the curated
 * lists and their record of rejected terms rather than from the cases, so the page shows the
 * criterion as it stands rather than as some past run applied it.
 *
 * The three are kept apart rather than merged into one list because they are not the same
 * claim. A term left off the list never runs at all; a gated term runs but cannot admit a
 * case on its own; an exclusion pattern rejects a document that other terms had already
 * matched. A reader auditing the method needs to know which of those applied.
 *
 * Everything here is server-supplied text and is rendered through `src/utils/caseText.ts`
 * like the rest of it. Native disclosures are used rather than scripted ones: they are
 * keyboard-operable and announced by screen readers without any code here having to get that
 * right, which is why `KeywordIndex` uses them too.
 */

import { useCallback, useState } from 'react'

import { getExclusions } from '@/api/client'
import type { ApiError } from '@/api/client'
import { useApiResource } from '@/hooks/useApiResource'
import type { ExclusionsResponse, JurisdictionExclusions } from '@/types/api'
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

/** One mechanism, and what it excludes in each jurisdiction. */
interface Mechanism {
  /** Stable identifier, used as the React key. */
  readonly id: string
  /** Disclosure heading. */
  readonly heading: string
  /** How to introduce {@link ExclusionRow.qualifier}; empty when it speaks for itself. */
  readonly qualifierLabel: string
  /** What a jurisdiction with nothing under this mechanism should say. */
  readonly emptyLabel: string
  readonly perJurisdiction: readonly MechanismJurisdiction[]
}

/**
 * Turn the payload into the three mechanisms, each grouped by jurisdiction.
 *
 * Jurisdictions keep the payload's order, which is the order the rest of the site lists them
 * in. Rows are sorted by subject: these run to dozens of entries, and alphabetical order is
 * the only one a reader can predict.
 *
 * @param payload - The `GET /api/exclusions` response.
 * @returns The three mechanisms, ready to render.
 */
function toMechanisms(payload: ExclusionsResponse): readonly Mechanism[] {
  const jurisdictions = payload.jurisdictions ?? []

  const build = (
    id: string,
    heading: string,
    qualifierLabel: string,
    emptyLabel: string,
    rowsOf: (entry: JurisdictionExclusions) => readonly ExclusionRow[],
  ): Mechanism => ({
    id,
    heading,
    qualifierLabel,
    emptyLabel,
    perJurisdiction: jurisdictions.map((entry) => ({
      code: entry.code,
      name: cleanInlineText(entry.name) || cleanInlineText(entry.code),
      rows: [...rowsOf(entry)].sort((a, b) => a.subject.localeCompare(b.subject)),
    })),
  })

  return [
    build(
      'left-off',
      'Terms deliberately left off the lists',
      '',
      'No term has been rejected for this jurisdiction yet.',
      (entry) =>
        (entry.excluded_terms ?? []).map((term) => ({
          key: term.id || term.term,
          subject: cleanInlineText(term.term),
          qualifier: categoryLabel(cleanInlineText(term.category)),
          reason: cleanInlineText(term.reason),
        })),
    ),
    build(
      'gated',
      'Gated terms',
      'only counts alongside',
      'No term is gated for this jurisdiction.',
      (entry) =>
        (entry.gated_terms ?? []).map((term) => ({
          key: term.id || term.term,
          subject: cleanInlineText(term.term),
          qualifier: (term.requires ?? []).map((gate) => cleanInlineText(gate)).join(', '),
          reason: '',
        })),
    ),
    build(
      'patterns',
      'Exclusion patterns',
      '',
      'No exclusion pattern is applied in this jurisdiction.',
      (entry) =>
        (entry.exclusion_patterns ?? []).map((pattern) => ({
          key: pattern.pattern,
          subject: cleanInlineText(pattern.pattern),
          qualifier: '',
          reason: cleanInlineText(pattern.reason),
        })),
    ),
  ]
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

/**
 * One mechanism's disclosure.
 *
 * @param props - Component properties.
 * @param props.mechanism - The mechanism to render.
 * @returns The disclosure.
 */
function MechanismDisclosure({ mechanism }: { readonly mechanism: Mechanism }): JSX.Element {
  const total = mechanism.perJurisdiction.reduce((sum, entry) => sum + entry.rows.length, 0)

  return (
    <details className="border-plt-border bg-plt-panel max-w-prose rounded border">
      <summary className="text-plt-accent-strong cursor-pointer rounded px-4 py-3 font-semibold">
        {mechanism.heading}
        <span className="text-plt-muted font-normal">
          {' — '}
          {String(total)} {total === 1 ? 'entry' : 'entries'}
        </span>
      </summary>
      <div className="border-plt-border space-y-5 border-t px-4 py-4">
        {mechanism.perJurisdiction.map((entry) => (
          <div key={entry.code} className="space-y-2">
            <h3 className="text-plt-ink text-sm font-semibold uppercase tracking-wide">
              {entry.name}
            </h3>
            {entry.rows.length === 0 ? (
              <p className="text-plt-muted text-sm">{mechanism.emptyLabel}</p>
            ) : (
              <ul className="text-plt-ink space-y-2 text-sm leading-relaxed">
                {entry.rows.map((row) => (
                  <li key={row.key}>
                    <span className="font-medium">{row.subject}</span>
                    {row.qualifier === '' ? null : (
                      <span className="text-plt-muted">
                        {' — '}
                        {mechanism.qualifierLabel === ''
                          ? row.qualifier
                          : `${mechanism.qualifierLabel} ${row.qualifier}`}
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

/** Properties of {@link ExclusionIndexLoader}. */
interface ExclusionIndexLoaderProps {
  /** Called by the retry control on the error state. */
  readonly onRetry: () => void
}

/**
 * Fetch the criteria and render whichever state applies.
 *
 * @param props - Component properties.
 * @returns The loading indicator, the error state, or the three disclosures.
 */
function ExclusionIndexLoader({ onRetry }: ExclusionIndexLoaderProps): JSX.Element {
  const load = useCallback((signal: AbortSignal) => getExclusions(signal), [])
  const { data, error, isLoading } = useApiResource(load)

  if (isLoading && data === null) {
    return (
      <p role="status" className="text-plt-muted max-w-prose leading-relaxed">
        Loading the exclusion criteria…
      </p>
    )
  }

  if (error !== null && data === null) {
    return (
      <div
        role="alert"
        className="border-plt-border bg-plt-accent-soft max-w-prose space-y-3 rounded border p-4"
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
    )
  }

  const jurisdictions = data?.jurisdictions ?? []

  if (jurisdictions.length === 0) {
    return (
      <p className="text-plt-muted max-w-prose leading-relaxed">
        The exclusion criteria are not available right now. They can be read in the project
        repository, under data/keywords.
      </p>
    )
  }

  return (
    <div className="space-y-4">
      {toMechanisms(data as ExclusionsResponse).map((mechanism) => (
        <MechanismDisclosure key={mechanism.id} mechanism={mechanism} />
      ))}
    </div>
  )
}

/**
 * Render the exclusion criteria, fetching them from the API.
 *
 * A retry does not patch state in place: it remounts {@link ExclusionIndexLoader} through a
 * `key` change, which restarts the fetch with the loader untouched and keeps
 * `useApiResource` the only owner of request state.
 *
 * @returns The three mechanism disclosures.
 */
export default function ExclusionIndex(): JSX.Element {
  const [attempt, setAttempt] = useState(0)

  return (
    <ExclusionIndexLoader
      key={attempt}
      onRetry={() => {
        setAttempt((previous) => previous + 1)
      }}
    />
  )
}
