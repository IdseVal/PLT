/**
 * One case in the All-cases listing.
 *
 * Everything shown here comes from a court's publication system and is therefore untrusted:
 * it is rendered as text through `cleanInlineText`, and the link to the source is checked
 * against the protocol allowlist in `src/utils/links.ts` before it reaches an `href`. There
 * is no `dangerouslySetInnerHTML` on this page or any other.
 *
 * The card carries what a reader needs to decide whether to open the judgment — identifier,
 * court, date, classification and the opening of the abstract — and no more. The abstract is
 * clamped rather than truncated in JavaScript, so the full text is still in the DOM for
 * find-in-page and for a screen reader.
 */

import { Link } from 'react-router-dom'

import { CHIP } from '@/components/cases/controls'
import { caseLabel, cleanInlineText } from '@/utils/caseText'
import { formatDecisionDate } from '@/utils/dates'
import { caseDetailPath, isSafeHref } from '@/utils/links'
import type { CaseSummary } from '@/types/api'

/** Properties of {@link CaseCard}. */
export interface CaseCardProps {
  /** The case to summarise. */
  readonly item: CaseSummary
}

/**
 * Render one result.
 *
 * @param props - Component properties.
 * @returns The card.
 */
export default function CaseCard({ item }: CaseCardProps): JSX.Element {
  const title = caseLabel(item.title, item.source_id)
  const abstract = cleanInlineText(item.abstract)
  const decisionDate = formatDecisionDate(item.decision_date) ?? ''
  const court = cleanInlineText(item.court_name)
  const jurisdiction = cleanInlineText(item.jurisdiction_name) || item.jurisdiction_code
  const sourceUrl = item.source_url ?? ''
  const language = cleanInlineText(item.language).toUpperCase()
  const topics = item.topics ?? []

  return (
    <article className="border-plt-border bg-plt-panel rounded-sm border p-4 sm:p-5">
      <h3 className="font-display text-lg font-bold leading-snug">
        <Link className="text-plt-accent-deep underline underline-offset-4" to={caseDetailPath(item.jurisdiction_code, item.source_id)}>
          {title}
        </Link>
      </h3>

      <p className="text-plt-muted mt-1 text-sm">
        <span>{jurisdiction}</span>
        {court === '' ? null : <span> &middot; {court}</span>}
        {decisionDate === '' ? null : (
          <>
            {' '}
            &middot;{' '}
            <time dateTime={item.decision_date ?? undefined}>{decisionDate}</time>
          </>
        )}
      </p>

      <p className="text-plt-muted mt-1 text-sm">
        <span className="font-medium">{cleanInlineText(item.source_id)}</span>
        {language === '' ? null : <span> &middot; {language}</span>}
      </p>

      {abstract === '' ? null : (
        <p className="text-plt-ink mt-3 line-clamp-3 max-w-prose text-sm leading-relaxed">
          {abstract}
        </p>
      )}

      {(item.law_domain ?? '') === '' && (item.law_subfield ?? '') === '' && topics.length === 0 ? null : (
        <ul className="mt-3 flex flex-wrap gap-2">
          {[item.law_domain, item.law_subfield]
            .map((value) => cleanInlineText(value))
            .filter((value) => value !== '')
            .map((value) => (
              <li key={value} className={CHIP}>
                {value}
              </li>
            ))}
          {topics.map((topic) => (
            <li key={topic.slug} className={CHIP}>
              {cleanInlineText(topic.label)}
            </li>
          ))}
        </ul>
      )}

      {isSafeHref(sourceUrl) ? (
        <p className="mt-3 text-sm">
          <a
            className="text-plt-accent-strong underline underline-offset-4"
            href={sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            Original publication
            <span className="sr-only"> of {title} (opens in a new tab)</span>
          </a>
        </p>
      ) : null}
    </article>
  )
}
