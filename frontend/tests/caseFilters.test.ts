/**
 * The filter state and its round trip through the URL.
 *
 * This is the layer that makes a filtered result set citable, so it is tested on its own:
 * what goes into the address bar has to come back out identical, and a URL that has been
 * mangled — by a truncated paste, an old bookmark or someone editing it by hand — has to
 * degrade to something sensible rather than being forwarded to the API.
 */

import { describe, expect, it } from 'vitest'

import {
  activeFilterCount,
  caseFiltersToSearchParams,
  isIsoDate,
  pageCount,
  parseCaseFilters,
  toExportQuery,
  toSearchQuery,
  withFilterChanges,
  DEFAULT_PAGE_SIZE,
  EMPTY_FILTERS,
} from '@/utils/caseFilters'

/**
 * Parse a query string.
 *
 * @param search - The query string, without the leading `?`.
 * @returns The parsed filter state.
 */
function parse(search: string): ReturnType<typeof parseCaseFilters> {
  return parseCaseFilters(new URLSearchParams(search))
}

describe('parseCaseFilters', () => {
  it('defaults to the whole collection, newest first', () => {
    expect(parse('')).toEqual(EMPTY_FILTERS)
  })

  it('restores every filter from the URL', () => {
    const filters = parse(
      'q=glyfosaat&jurisdiction=NL&jurisdiction=EU&law_domain=public&law_subfield=administrative' +
        '&topic=spray-zones&court=7&language=nl&date_from=2020-01-01&date_to=2024-12-31' +
        '&sort=date_asc&page=3&page_size=50',
    )

    expect(filters).toEqual({
      q: 'glyfosaat',
      jurisdiction: ['NL', 'EU'],
      law_domain: 'public',
      law_subfield: 'administrative',
      topic: 'spray-zones',
      court: '7',
      language: 'nl',
      date_from: '2020-01-01',
      date_to: '2024-12-31',
      sort: 'date_asc',
      page: 3,
      page_size: 50,
    })
  })

  it('keeps repeated jurisdictions distinct and drops empty ones', () => {
    expect(parse('jurisdiction=NL&jurisdiction=NL&jurisdiction=&jurisdiction=EU').jurisdiction).toEqual([
      'NL',
      'EU',
    ])
  })

  it('falls back to the default sort for an unknown sort key', () => {
    expect(parse('sort=by_length').sort).toBe('date_desc')
  })

  it.each(['0', '-4', 'two', '1e6', ''])('falls back to page 1 for page=%s', (value) => {
    expect(parse(`page=${value}`).page).toBe(1)
  })

  it('clamps a page size above the documented maximum back to the default', () => {
    expect(parse('page_size=100000').page_size).toBe(DEFAULT_PAGE_SIZE)
    expect(parse('page_size=100').page_size).toBe(100)
  })

  it.each(['yesterday', '2024-13-01', '2024-02-31', '05-03-2024'])(
    'drops the unusable date %s rather than sending it to the API',
    (value) => {
      expect(parse(`date_from=${value}`).date_from).toBe('')
    },
  )

  it('bounds a pathologically long query', () => {
    expect(parse(`q=${'a'.repeat(5000)}`).q).toHaveLength(200)
  })
})

describe('caseFiltersToSearchParams', () => {
  it('round-trips a selection unchanged', () => {
    const original = parse('q=glyfosaat&jurisdiction=NL&jurisdiction=EU&court=7&sort=relevance&page=4')

    expect(parseCaseFilters(caseFiltersToSearchParams(original))).toEqual(original)
  })

  it('omits defaults, so an unfiltered listing has a clean URL', () => {
    expect(caseFiltersToSearchParams(EMPTY_FILTERS).toString()).toBe('')
  })

  it('emits the same string for the same selection whatever order it was built in', () => {
    const first = caseFiltersToSearchParams(
      withFilterChanges(EMPTY_FILTERS, { court: '7', q: 'glyfosaat' }),
    )
    const second = caseFiltersToSearchParams(
      withFilterChanges(withFilterChanges(EMPTY_FILTERS, { q: 'glyfosaat' }), { court: '7' }),
    )

    expect(first.toString()).toBe(second.toString())
  })
})

describe('withFilterChanges', () => {
  it('returns to the first page when a filter changes', () => {
    const onPageSeven = parse('page=7&q=glyfosaat')

    expect(withFilterChanges(onPageSeven, { law_domain: 'public' }).page).toBe(1)
  })

  it('keeps the page when the page itself is what changed', () => {
    expect(withFilterChanges(parse('q=glyfosaat'), { page: 5 }).page).toBe(5)
  })
})

describe('query building', () => {
  it('sends every filter to the search endpoint', () => {
    const query = toSearchQuery(parse('q=glyfosaat&jurisdiction=NL&page=2&page_size=50'))

    expect(query).toMatchObject({ q: 'glyfosaat', jurisdiction: ['NL'], page: 2, page_size: 50 })
  })

  it('sends the filters but not the paging to the export endpoint', () => {
    const query = toExportQuery(parse('q=glyfosaat&jurisdiction=NL&page=2&page_size=50'))

    expect(query).toMatchObject({ q: 'glyfosaat', jurisdiction: ['NL'] })
    expect(query.page).toBeUndefined()
    expect(query.page_size).toBeUndefined()
  })
})

describe('counting', () => {
  it('counts the filters that narrow the collection, and not sort or paging', () => {
    expect(activeFilterCount(parse('sort=date_asc&page=3&page_size=50'))).toBe(0)
    expect(activeFilterCount(parse('q=glyfosaat&jurisdiction=NL&jurisdiction=EU&court=7'))).toBe(4)
  })

  it('always reports at least one page', () => {
    expect(pageCount(0, 20)).toBe(1)
    expect(pageCount(41, 20)).toBe(3)
    expect(pageCount(40, 20)).toBe(2)
  })

  it('accepts only real calendar dates', () => {
    expect(isIsoDate('2024-02-29')).toBe(true)
    expect(isIsoDate('2023-02-29')).toBe(false)
  })
})
