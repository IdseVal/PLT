/**
 * Date presentation helpers.
 *
 * Dates arrive from the API as ISO-8601 strings and are rendered for a reader, so the two
 * forms are kept apart: the ISO string goes in `<time datetime>` for machines, the formatted
 * string in the element's text for people.
 */

/**
 * Fixed locale and time zone.
 *
 * The site is English, and a decision date is a calendar date in the court's own record
 * rather than an instant: formatting it in the reader's local zone would move it a day for
 * anyone west of UTC. Both are therefore pinned, which also keeps tests deterministic.
 */
const DECISION_DATE_FORMAT = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  timeZone: 'UTC',
})

/** Counts are formatted in the same fixed locale, so a result total reads alike for everyone. */
const COUNT_FORMAT = new Intl.NumberFormat('en-GB')

/**
 * Format a decision date for display.
 *
 * @param value - ISO-8601 date or timestamp, or `null` when the source gave none.
 * @returns The formatted date, or `null` when there is nothing valid to show. Callers render
 *   their own fallback rather than a misleading placeholder date.
 */
export function formatDecisionDate(value: string | null): string | null {
  if (value === null || value.trim() === '') return null

  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return null

  return DECISION_DATE_FORMAT.format(parsed)
}

/**
 * Format a whole number with thousands separators, for result counts and page numbers.
 *
 * @param value - The number to format.
 * @returns The formatted number, e.g. `1,284`.
 */
export function formatCount(value: number): string {
  return COUNT_FORMAT.format(value)
}
