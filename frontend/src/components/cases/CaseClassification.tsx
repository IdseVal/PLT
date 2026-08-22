/**
 * The classification block of a case.
 *
 * This is the classification of `docs/CORE_DOCUMENT.md` section 2.2 — jurisdiction, law
 * domain, law subfield, litigating parties, the filing and decision dates, and topic — plus
 * the source metadata section 2.2 goes on to require the pipeline to keep: court, procedure
 * type, case numbers, publication date, language and outcome.
 *
 * A description list is the right element for it: each label is programmatically tied to its
 * value, so a screen reader reads "Court: Hoge Raad" rather than two unrelated fragments.
 * Rows with nothing in them are left out rather than rendered empty, because a source that
 * does not publish a filing date is not the same as a case without one.
 *
 * Every value is untrusted source data and is rendered as text.
 */

import { Link } from 'react-router-dom'

import { CHIP } from '@/components/cases/controls'
import { categoryLabel, cleanInlineText } from '@/utils/caseText'
import { formatDecisionDate } from '@/utils/dates'
import type { CaseRecord, KeywordMatchRef, PartyRef } from '@/types/api'

/** The keyword and category labels of a case, deduplicated and ordered for display. */
interface CaseLabels {
  readonly keywords: readonly { readonly term_id: string; readonly term: string }[]
  readonly categories: readonly string[]
}

/**
 * Reduce a case's matches to the labels it is listed under.
 *
 * The API returns one match per selecting term already, but a term with no text and a
 * repeated category are both possible, so this deduplicates rather than trusting the shape.
 * Keywords keep the order they were found in; categories are sorted, because their order
 * carries no meaning and a stable one makes two cases comparable at a glance.
 *
 * @param matches - The case's keyword matches.
 * @returns The labels to render.
 */
function caseLabels(matches: readonly KeywordMatchRef[]): CaseLabels {
  const keywords = new Map<string, string>()
  const categories = new Set<string>()

  for (const match of matches) {
    const term = cleanInlineText(match.term)
    if (term !== '' && !keywords.has(match.term_id)) keywords.set(match.term_id, term)
    const category = cleanInlineText(match.category)
    if (category !== '') categories.add(category)
  }

  return {
    keywords: [...keywords].map(([term_id, term]) => ({ term_id, term })),
    categories: [...categories].sort((a, b) => a.localeCompare(b)),
  }
}

/** Properties of {@link CaseClassification}. */
export interface CaseClassificationProps {
  /** The case being described. */
  readonly item: CaseRecord
}

/** Party roles in the order a case is normally read, with their labels. */
const PARTY_ROLES: readonly { readonly role: string; readonly label: string }[] = [
  { role: 'applicant', label: 'Applicant' },
  { role: 'defendant', label: 'Defendant' },
  { role: 'intervener', label: 'Intervener' },
  { role: 'other', label: 'Other party' },
]

/** Properties of {@link Row}. */
interface RowProps {
  readonly label: string
  readonly children: React.ReactNode
}

/**
 * One label-and-value row.
 *
 * @param props - Component properties.
 * @returns The row.
 */
function Row({ label, children }: RowProps): JSX.Element {
  return (
    <div className="border-plt-border border-b py-2 last:border-b-0 sm:grid sm:grid-cols-3 sm:gap-4">
      <dt className="text-plt-muted text-sm font-medium">{label}</dt>
      <dd className="text-plt-ink mt-1 text-sm sm:col-span-2 sm:mt-0">{children}</dd>
    </div>
  )
}

/**
 * Group parties by their role, keeping the reading order and appending unknown roles.
 *
 * @param parties - Parties as the API returned them.
 * @returns Non-empty groups, labelled.
 */
function groupParties(parties: readonly PartyRef[]): { label: string; names: string[] }[] {
  const known = PARTY_ROLES.map(({ role, label }) => ({
    label,
    names: parties
      .filter((party) => party.role === role)
      .map((party) => cleanInlineText(party.name))
      .filter((name) => name !== ''),
  }))

  const unknown = parties.filter(
    (party) => !PARTY_ROLES.some((candidate) => candidate.role === party.role),
  )
  if (unknown.length > 0) {
    known.push({
      label: 'Party',
      names: unknown.map((party) => cleanInlineText(party.name)).filter((name) => name !== ''),
    })
  }

  return known.filter((group) => group.names.length > 0)
}

/**
 * A date row, rendered as a machine-readable `<time>`.
 *
 * @param props - Component properties.
 * @param props.label - Row label.
 * @param props.value - ISO date, or `null`.
 * @returns The row, or `null` when the source published no such date.
 */
function DateRow({ label, value }: { readonly label: string; readonly value: string | null | undefined }): JSX.Element | null {
  const formatted = formatDecisionDate(value ?? null) ?? ''
  if (formatted === '') return null

  return (
    <Row label={label}>
      <time dateTime={value ?? undefined}>{formatted}</time>
    </Row>
  )
}

/**
 * A plain text row.
 *
 * @param props - Component properties.
 * @param props.label - Row label.
 * @param props.value - Untrusted value from the source.
 * @returns The row, or `null` when there is nothing to show.
 */
function TextRow({ label, value }: { readonly label: string; readonly value: string | null | undefined }): JSX.Element | null {
  const text = cleanInlineText(value)
  if (text === '') return null

  return <Row label={label}>{text}</Row>
}

/**
 * Render the classification block.
 *
 * @param props - Component properties.
 * @returns The description list.
 */
export default function CaseClassification({ item }: CaseClassificationProps): JSX.Element {
  const parties = groupParties(item.parties ?? [])
  const topics = item.topics ?? []
  const { keywords, categories } = caseLabels(item.keyword_matches ?? [])
  const caseNumbers = (item.case_numbers ?? [])
    .map((number) => cleanInlineText(number))
    .filter((number) => number !== '')
  const language = cleanInlineText(item.language).toUpperCase()

  return (
    <dl className="mt-2">
      <TextRow
        label="Jurisdiction"
        value={cleanInlineText(item.jurisdiction_name) || item.jurisdiction_code}
      />
      <TextRow label="Court" value={item.court_name} />
      <TextRow label="Procedure" value={item.procedure_type} />
      <DateRow label="Date of filing" value={item.filing_date} />
      <DateRow label="Date of decision" value={item.decision_date} />
      <DateRow label="Date of publication" value={item.publication_date} />
      <TextRow label="Outcome" value={item.outcome} />

      {caseNumbers.length === 0 ? null : (
        <Row label={caseNumbers.length === 1 ? 'Case number' : 'Case numbers'}>
          <ul className="space-y-1">
            {caseNumbers.map((number) => (
              <li key={number}>{number}</li>
            ))}
          </ul>
        </Row>
      )}

      <TextRow label="Source identifier" value={item.source_id} />

      {parties.length === 0
        ? null
        : parties.map((group) => (
            <Row key={group.label} label={group.label}>
              <ul className="space-y-1">
                {group.names.map((name) => (
                  <li key={name}>{name}</li>
                ))}
              </ul>
            </Row>
          ))}

      <TextRow label="Law domain" value={item.law_domain} />
      <TextRow label="Law subfield" value={item.law_subfield} />

      {topics.length === 0 ? null : (
        <Row label="Topics">
          <ul className="flex flex-wrap gap-2">
            {topics.map((topic) => (
              <li key={topic.slug} className={CHIP}>
                {cleanInlineText(topic.label)}
              </li>
            ))}
          </ul>
        </Row>
      )}

      {keywords.length === 0 ? null : (
        <Row label={keywords.length === 1 ? 'Keyword' : 'Keywords'}>
          <ul className="flex flex-wrap gap-2">
            {keywords.map((keyword) => (
              <li key={keyword.term_id}>
                <Link className={CHIP} to={`/cases?keyword=${encodeURIComponent(keyword.term_id)}`}>
                  {keyword.term}
                </Link>
              </li>
            ))}
          </ul>
        </Row>
      )}

      {categories.length === 0 ? null : (
        <Row label={categories.length === 1 ? 'Category' : 'Categories'}>
          <ul className="flex flex-wrap gap-2">
            {categories.map((category) => (
              <li key={category}>
                <Link className={CHIP} to={`/cases?category=${encodeURIComponent(category)}`}>
                  {categoryLabel(category)}
                </Link>
              </li>
            ))}
          </ul>
        </Row>
      )}

      {language === '' ? null : <Row label="Language">{language}</Row>}
    </dl>
  )
}
