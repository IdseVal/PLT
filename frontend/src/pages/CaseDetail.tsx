/**
 * One case: abstract, classification, full text in the original language, and a link out to
 * the court's own publication.
 *
 * This is the page the tracker exists for. Core document section 2.1 puts comfortable
 * reading of case law first, so the judgment gets the page: the classification is a compact
 * block above it, and the text itself runs in a single constrained column rather than
 * competing with a sidebar.
 *
 * A case is addressed by jurisdiction and source identifier, both of which arrive from the
 * URL and are therefore untrusted. They are encoded before they reach the API
 * (`src/api/client.ts`), an identifier that matches no case renders a not-found page rather
 * than a crash, and every string that comes back is rendered as text — never as markup. See
 * `src/utils/caseText.ts`.
 */

import { useCallback } from 'react'
import { Link, useParams } from 'react-router-dom'

import { getCase } from '@/api/client'
import CaseClassification from '@/components/cases/CaseClassification'
import CaseFullText from '@/components/cases/CaseFullText'
import { LINK, PANEL } from '@/components/cases/controls'
import { useApiResource } from '@/hooks/useApiResource'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

import { caseLabel, cleanInlineText, toParagraphs } from '@/utils/caseText'
import { formatDecisionDate } from '@/utils/dates'
import { isSafeHref } from '@/utils/links'

/** Properties of {@link CaseNotFound}. */
interface CaseNotFoundProps {
  /** The identifier that was asked for, echoed back so a typo is visible. */
  readonly sourceId: string
  /** The jurisdiction that was asked for. */
  readonly jurisdiction: string
}

/**
 * The page shown when no case has the requested identifier.
 *
 * A mistyped or retired ECLI is an ordinary event — links to case law are pasted between
 * documents for years — so it gets a real page with a way onwards, not an error.
 *
 * @param props - Component properties.
 * @returns The not-found page.
 */
function CaseNotFound({ sourceId, jurisdiction }: CaseNotFoundProps): JSX.Element {
  return (
    <section className="space-y-4">
      <h1 className="text-plt-accent-deep font-display text-3xl font-bold sm:text-4xl">
        Case not found
      </h1>
      <p className="text-plt-muted max-w-prose leading-relaxed">
        No case in the tracker has the identifier <strong className="text-plt-ink">{sourceId}</strong>{' '}
        in jurisdiction <strong className="text-plt-ink">{jurisdiction}</strong>. The tracker
        holds only the judgments its keyword filters selected, so a case that exists in the
        court's own database may not be here; an ECLI or CELEX number that has been mistyped
        will also land on this page.
      </p>
      <p>
        <Link className={LINK} to="/cases">
          Browse all cases
        </Link>
      </p>
    </section>
  )
}

/**
 * The case detail page.
 *
 * @returns The case, a not-found page, or the failure that stopped it loading.
 */
export default function CaseDetail(): JSX.Element {
  const params = useParams<{ jurisdiction: string; sourceId: string }>()
  const jurisdiction = params.jurisdiction ?? ''
  const sourceId = params.sourceId ?? ''

  const loadCase = useCallback(
    (signal: AbortSignal) => getCase(jurisdiction, sourceId, signal),
    [jurisdiction, sourceId],
  )
  const { data, error, isLoading } = useApiResource(loadCase)

  const heading = data === null ? cleanInlineText(sourceId) : caseLabel(data.title, data.source_id)
  useDocumentTitle(error?.status === 404 ? 'Case not found' : heading)

  if (error !== null && error.status === 404) {
    return <CaseNotFound sourceId={cleanInlineText(sourceId)} jurisdiction={cleanInlineText(jurisdiction)} />
  }

  if (error !== null) {
    return (
      <section className="space-y-4">
        <h1 className="text-plt-accent-deep font-display text-3xl font-bold sm:text-4xl">
          {heading}
        </h1>
        <p role="alert" className="text-plt-ink max-w-prose leading-relaxed">
          {error.isNetworkError
            ? 'The tracker could not be reached, so this case could not be loaded. Check your connection and try again.'
            : error.message}
        </p>
        <p>
          <Link className={LINK} to="/cases">
            Browse all cases
          </Link>
        </p>
      </section>
    )
  }

  if (data === null) {
    return (
      <section className="space-y-4" aria-busy={isLoading}>
        <h1 className="text-plt-accent-deep font-display text-3xl font-bold sm:text-4xl">
          {heading}
        </h1>
        <p className="text-plt-muted">Loading this case…</p>
      </section>
    )
  }

  const abstract = toParagraphs(data.abstract)
  const decisionDate = formatDecisionDate(data.decision_date) ?? ''
  const court = cleanInlineText(data.court_name)
  const jurisdictionName = cleanInlineText(data.jurisdiction_name) || data.jurisdiction_code
  const sourceUrl = data.source_url ?? ''

  return (
    <article className="space-y-8">
      <header className="space-y-3">
        <p className="text-plt-muted text-sm">
          <Link className={LINK} to="/cases">
            All cases
          </Link>
          <span aria-hidden="true"> / </span>
          <span>{jurisdictionName}</span>
        </p>

        <h1 className="text-plt-accent-deep font-display max-w-prose text-3xl font-bold leading-tight sm:text-4xl">
          {heading}
        </h1>

        <p className="text-plt-muted text-sm">
          {court === '' ? null : <span>{court}</span>}
          {court === '' || decisionDate === '' ? null : <span> &middot; </span>}
          {decisionDate === '' ? null : (
            <time dateTime={data.decision_date ?? undefined}>{decisionDate}</time>
          )}
        </p>

        {isSafeHref(sourceUrl) ? (
          <p>
            <a className={LINK} href={sourceUrl} target="_blank" rel="noopener noreferrer">
              Read this case on the original publication page
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          </p>
        ) : (
          <p className="text-plt-muted text-sm">
            This case carries no link to an original publication page.
          </p>
        )}
      </header>

      {abstract.length === 0 ? null : (
        <section aria-labelledby="case-abstract" className={`${PANEL} p-4 sm:p-6`}>
          <h2 id="case-abstract" className="text-plt-accent-deep font-display text-xl font-bold">
            Abstract
          </h2>
          <div className="text-plt-ink mt-3 max-w-prose space-y-3 leading-relaxed">
            {abstract.map((paragraph, index) => (
              // Stable key: the abstract is one immutable string, split once.
              <p key={index} className="whitespace-pre-line">
                {paragraph}
              </p>
            ))}
          </div>
        </section>
      )}

      <section aria-labelledby="case-classification" className={`${PANEL} p-4 sm:p-6`}>
        <h2 id="case-classification" className="text-plt-accent-deep font-display text-xl font-bold">
          Classification
        </h2>
        <CaseClassification item={data} />
      </section>

      <section aria-labelledby="case-full-text">
        <h2 id="case-full-text" className="text-plt-accent-deep font-display text-xl font-bold">
          Full text
        </h2>
        <p className="text-plt-muted mb-6 mt-2 max-w-prose text-sm leading-relaxed">
          Published in the language the court issued it in, as the tracker received it from the
          source. Formatting is not reproduced.
        </p>
        <CaseFullText documents={data.documents ?? []} caseLanguage={data.language} />
      </section>
    </article>
  )
}
