/**
 * The All-cases filter state, and its round trip through the URL query string.
 *
 * The query string is the single source of truth for what the listing shows. That is a
 * requirement rather than an implementation choice: a filtered view has to be linkable and
 * has to survive a reload, so that a result set can be cited in a paper. Nothing about the
 * selection is held in component state that the URL does not also carry.
 *
 * Everything here comes from outside the application — a pasted link, a bookmark, a search
 * engine — so it is treated as untrusted. Values are validated and bounded on the way in:
 * an unknown sort key, a page number of `-1`, a page size of `100000` or a date of
 * `yesterday` fall back to the default rather than reaching the API. The server validates
 * again (`docs/architecture.md` section 5); this layer exists so a mangled URL still renders
 * a sensible page.
 */

import type { CaseSort } from '@/types/api'
import type { QueryValue } from '@/api/client'

/** Page size the API uses when none is given (`docs/architecture.md` section 5). */
export const DEFAULT_PAGE_SIZE = 20

/** Largest page size the API accepts. */
export const MAX_PAGE_SIZE = 100

/** Page sizes offered in the UI. Every value must be at most {@link MAX_PAGE_SIZE}. */
export const PAGE_SIZE_OPTIONS: readonly number[] = [20, 50, 100]

/** Sort orders, with the label shown in the control. */
export const SORT_OPTIONS: readonly { readonly value: CaseSort; readonly label: string }[] = [
  { value: 'date_desc', label: 'Newest decision first' },
  { value: 'date_asc', label: 'Oldest decision first' },
  { value: 'relevance', label: 'Best match' },
]

/** Default sort, matching the API's own default. */
export const DEFAULT_SORT: CaseSort = 'date_desc'

/** Longest free-text query accepted, so a pathological URL cannot be forwarded verbatim. */
const MAX_QUERY_LENGTH = 200

/** Longest value accepted for a facet parameter such as a court id or a topic slug. */
const MAX_FACET_LENGTH = 128

/** Largest page number accepted, which bounds the offset the API is asked for. */
const MAX_PAGE = 100_000

/** ISO 8601 calendar date, the only date format the API takes. */
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

/** Control characters, stripped from free text before it is put back into a URL. */
// eslint-disable-next-line no-control-regex
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f]/g

/**
 * One filtered, sorted, paginated view of the collection.
 *
 * Field names match the query parameters in `docs/architecture.md` section 5 exactly, so the
 * state can be handed to the API client without a mapping table. Only `jurisdiction`
 * repeats; the contract makes every other filter single-valued.
 */
export interface CaseFilterState {
  /** Full-text query. */
  readonly q: string
  /** Jurisdiction codes; repeatable, so several can be selected at once. */
  readonly jurisdiction: readonly string[]
  readonly law_domain: string
  readonly law_subfield: string
  /** Topic slug. */
  readonly topic: string
  /**
   * Id of a curated keyword term the case is labelled with, e.g. `nl-glyfosaat`.
   *
   * The id rather than the term text: the text is written to be read and may be re-worded,
   * while the id is the stable thing a cited link has to survive.
   */
  readonly keyword: string
  /** Keyword category the case carries a label in, e.g. `active_substance`. */
  readonly category: string
  /** Court id, as a string because it travels through a `<select>`. */
  readonly court: string
  /** ISO 639-1 language code. */
  readonly language: string
  /** Inclusive lower bound on the decision date, `YYYY-MM-DD`. */
  readonly date_from: string
  /** Inclusive upper bound on the decision date, `YYYY-MM-DD`. */
  readonly date_to: string
  readonly sort: CaseSort
  /** One-based page number. */
  readonly page: number
  readonly page_size: number
}

/** The unfiltered view: every published case, newest decision first, first page. */
export const EMPTY_FILTERS: CaseFilterState = {
  q: '',
  jurisdiction: [],
  law_domain: '',
  law_subfield: '',
  topic: '',
  keyword: '',
  category: '',
  court: '',
  language: '',
  date_from: '',
  date_to: '',
  sort: DEFAULT_SORT,
  page: 1,
  page_size: DEFAULT_PAGE_SIZE,
}

/** Filter keys that hold a single string, in the order they appear in the URL. */
const SINGLE_VALUE_KEYS = [
  'law_domain',
  'law_subfield',
  'topic',
  'keyword',
  'category',
  'court',
  'language',
] as const

/** A single-valued filter key. */
export type SingleValueFilterKey = (typeof SINGLE_VALUE_KEYS)[number]

/**
 * Trim a string, drop control characters and cut it to a maximum length.
 *
 * @param value - Raw value, or `null` when the parameter was absent.
 * @param maxLength - Longest result to return.
 * @returns The cleaned value, possibly empty.
 */
function cleanString(value: string | null, maxLength: number): string {
  if (value === null) return ''
  return value.replace(CONTROL_CHARACTERS, '').trim().slice(0, maxLength)
}

/**
 * Whether a string is a calendar date the API will accept.
 *
 * `2024-02-31` matches the pattern but is not a date, so the round trip through `Date` is
 * what actually decides.
 *
 * @param value - Candidate date.
 * @returns `true` for a real `YYYY-MM-DD` date.
 */
export function isIsoDate(value: string): boolean {
  if (!ISO_DATE.test(value)) return false
  const parsed = new Date(`${value}T00:00:00Z`)
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().startsWith(value)
}

/**
 * Read a bounded positive integer from a query parameter.
 *
 * @param value - Raw value, or `null`.
 * @param fallback - Value to use when the parameter is absent or unusable.
 * @param min - Smallest accepted value.
 * @param max - Largest accepted value.
 * @returns The parsed value, or `fallback`.
 */
function cleanInteger(value: string | null, fallback: number, min: number, max: number): number {
  if (value === null || !/^\d{1,7}$/.test(value.trim())) return fallback
  const parsed = Number.parseInt(value, 10)
  if (!Number.isFinite(parsed) || parsed < min || parsed > max) return fallback
  return parsed
}

/**
 * Read the filter state out of a URL query string.
 *
 * @param params - The query parameters of the current location.
 * @returns A fully populated, validated filter state.
 */
export function parseCaseFilters(params: URLSearchParams): CaseFilterState {
  const jurisdiction = [
    ...new Set(
      params
        .getAll('jurisdiction')
        .map((value) => cleanString(value, MAX_FACET_LENGTH))
        .filter((value) => value !== ''),
    ),
  ]

  const sortParam = params.get('sort')
  const sort = SORT_OPTIONS.find((option) => option.value === sortParam)?.value ?? DEFAULT_SORT

  const dateFrom = cleanString(params.get('date_from'), 10)
  const dateTo = cleanString(params.get('date_to'), 10)

  return {
    q: cleanString(params.get('q'), MAX_QUERY_LENGTH),
    jurisdiction,
    law_domain: cleanString(params.get('law_domain'), MAX_FACET_LENGTH),
    law_subfield: cleanString(params.get('law_subfield'), MAX_FACET_LENGTH),
    topic: cleanString(params.get('topic'), MAX_FACET_LENGTH),
    keyword: cleanString(params.get('keyword'), MAX_FACET_LENGTH),
    category: cleanString(params.get('category'), MAX_FACET_LENGTH),
    court: cleanString(params.get('court'), MAX_FACET_LENGTH),
    language: cleanString(params.get('language'), MAX_FACET_LENGTH),
    date_from: isIsoDate(dateFrom) ? dateFrom : '',
    date_to: isIsoDate(dateTo) ? dateTo : '',
    sort,
    page: cleanInteger(params.get('page'), 1, 1, MAX_PAGE),
    page_size: cleanInteger(params.get('page_size'), DEFAULT_PAGE_SIZE, 1, MAX_PAGE_SIZE),
  }
}

/**
 * Write the filter state back into a query string.
 *
 * Defaults are omitted and the keys are emitted in a fixed order, so the same selection
 * always produces character-for-character the same URL. That is what makes a filtered view
 * safe to cite: two readers who build the same selection get the same link.
 *
 * @param filters - The state to serialise.
 * @returns Parameters ready to hand to the router.
 */
export function caseFiltersToSearchParams(filters: CaseFilterState): URLSearchParams {
  const params = new URLSearchParams()

  if (filters.q !== '') params.set('q', filters.q)
  for (const code of filters.jurisdiction) params.append('jurisdiction', code)
  for (const key of SINGLE_VALUE_KEYS) {
    if (filters[key] !== '') params.set(key, filters[key])
  }
  if (filters.date_from !== '') params.set('date_from', filters.date_from)
  if (filters.date_to !== '') params.set('date_to', filters.date_to)
  if (filters.sort !== DEFAULT_SORT) params.set('sort', filters.sort)
  if (filters.page !== 1) params.set('page', String(filters.page))
  if (filters.page_size !== DEFAULT_PAGE_SIZE) params.set('page_size', String(filters.page_size))

  return params
}

/**
 * Apply a partial change, returning to page 1 unless the change is the page itself.
 *
 * Changing a filter while on page 7 of the old result set would otherwise show an empty
 * page for no reason the reader can see.
 *
 * @param filters - Current state.
 * @param patch - Fields to change.
 * @returns The new state.
 */
export function withFilterChanges(
  filters: CaseFilterState,
  patch: Partial<CaseFilterState>,
): CaseFilterState {
  const next = { ...filters, ...patch }
  return 'page' in patch ? next : { ...next, page: 1 }
}

/**
 * Query parameters for `GET /api/cases`.
 *
 * @param filters - The active selection.
 * @returns Parameters for the API client, with empty values dropped by the client itself.
 */
export function toSearchQuery(filters: CaseFilterState): Record<string, QueryValue> {
  return {
    q: filters.q,
    jurisdiction: [...filters.jurisdiction],
    law_domain: filters.law_domain,
    law_subfield: filters.law_subfield,
    topic: filters.topic,
    keyword: filters.keyword,
    category: filters.category,
    court: filters.court,
    language: filters.language,
    date_from: filters.date_from,
    date_to: filters.date_to,
    sort: filters.sort,
    page: filters.page,
    page_size: filters.page_size,
  }
}

/**
 * Query parameters for `GET /api/cases/export`.
 *
 * Identical to the search query minus the paging, because an export covers the whole
 * selection rather than the page currently on screen.
 *
 * @param filters - The active selection.
 * @returns Parameters for the export link.
 */
export function toExportQuery(filters: CaseFilterState): Record<string, QueryValue> {
  const query = toSearchQuery(filters)
  delete query.page
  delete query.page_size
  return query
}

/**
 * How many filters are narrowing the result set, ignoring sort and paging.
 *
 * @param filters - The active selection.
 * @returns The number of active filters, for the "Filters (3)" affordance and for deciding
 *   whether a "clear all" control is worth showing.
 */
export function activeFilterCount(filters: CaseFilterState): number {
  let count = filters.jurisdiction.length
  if (filters.q !== '') count += 1
  for (const key of SINGLE_VALUE_KEYS) {
    if (filters[key] !== '') count += 1
  }
  if (filters.date_from !== '') count += 1
  if (filters.date_to !== '') count += 1
  return count
}

/**
 * The number of pages a total spans at the given page size.
 *
 * @param total - Total matching cases.
 * @param pageSize - Results per page.
 * @returns At least 1, so an empty result set still has a "page 1 of 1".
 */
export function pageCount(total: number, pageSize: number): number {
  if (pageSize <= 0) return 1
  return Math.max(1, Math.ceil(total / pageSize))
}
