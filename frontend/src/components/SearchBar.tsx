/**
 * Home-page search bar.
 *
 * Submitting navigates to `/cases?q=…`; the home page never renders results in place
 * (`docs/architecture.md` section 6). That keeps one page responsible for searching,
 * filtering and pagination, and makes every result set a shareable URL.
 *
 * The hint beneath the field carries a link through to the same page unsearched. A reader who
 * does not yet have a term in mind — which is most of them, on a database of a subject rather
 * than of a case they already know — otherwise has to guess a word before the collection will
 * show them anything. The sidebar's "All cases" button goes to the same place; this is the
 * route for someone whose eye is on the search field, and it names what is there.
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { createSearchParams, Link, useNavigate } from 'react-router-dom'

import { LINK } from '@/components/cases/controls'

/** Identifier tying the visible label to the field. */
const FIELD_ID = 'home-search-query'

/**
 * Render the search form.
 *
 * @returns The search bar.
 */
export default function SearchBar(): JSX.Element {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')

  /**
   * Send the reader to the all-cases page with their query.
   *
   * The value is put through `URLSearchParams`, so whatever was typed is encoded rather than
   * concatenated into a URL. An empty query goes to the unfiltered listing instead of
   * searching for nothing.
   *
   * @param event - The form submission.
   */
  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const trimmed = query.trim()
    navigate(trimmed === '' ? '/cases' : `/cases?${createSearchParams({ q: trimmed }).toString()}`)
  }

  return (
    <form role="search" aria-label="Search the case law" onSubmit={handleSubmit} className="space-y-2">
      <label htmlFor={FIELD_ID} className="text-plt-ink block text-sm font-semibold">
        Search the collection
      </label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          id={FIELD_ID}
          name="q"
          type="search"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value)
          }}
          placeholder="Glyphosate, plant protection products, an ECLI or CELEX number…"
          autoComplete="off"
          aria-describedby={`${FIELD_ID}-hint`}
          className="border-plt-border bg-plt-panel text-plt-ink placeholder:text-plt-muted w-full flex-1 rounded border px-4 py-3 text-base shadow-sm"
        />
        <button
          type="submit"
          className="bg-plt-accent-deep text-plt-inverse rounded px-6 py-3 text-base font-semibold"
        >
          Search
        </button>
      </div>
      <p id={`${FIELD_ID}-hint`} className="text-plt-muted text-sm">
        Searches case titles, abstracts and full texts across every jurisdiction in the tracker.{' '}
        <Link className={LINK} to="/cases">
          Browse all cases
        </Link>{' '}
        to filter by jurisdiction, court, keyword, category or date instead.
      </p>
    </form>
  )
}
