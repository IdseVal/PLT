/**
 * Dates as a reader wants to see them.
 *
 * The API sends ISO calendar dates (`2024-03-05`). A citation reads better as
 * "5 March 2024", so dates are formatted for display and kept in the machine-readable form
 * in the `datetime` attribute of a `<time>` element, where a citation manager can find it.
 *
 * Formatting is pinned to `en-GB` and to UTC rather than to the reader's locale and time
 * zone. A decision date is a calendar fact, not a moment: rendering it in a zone behind UTC
 * would show the day before, and letting the locale vary would make two readers cite the
 * same judgment differently.
 */

/** Locale every date on the site is formatted in. */
const DISPLAY_LOCALE = 'en-GB'

/** Long form, for the classification block and case headers. */
const LONG_DATE = new Intl.DateTimeFormat(DISPLAY_LOCALE, {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  timeZone: 'UTC',
})

/**
 * Format an ISO calendar date for reading.
 *
 * @param value - ISO `YYYY-MM-DD` date, or `null`/`undefined` when the source had none.
 * @returns The formatted date, or an empty string when there is no usable date. An
 *   unparseable value is returned unchanged rather than dropped, so a malformed date from a
 *   source is visible instead of silently disappearing.
 */
export function formatIsoDate(value: string | null | undefined): string {
  if (value === null || value === undefined || value.trim() === '') return ''

  const parsed = new Date(`${value}T00:00:00Z`)
  if (Number.isNaN(parsed.getTime())) return value

  return LONG_DATE.format(parsed)
}

/**
 * Format a whole number with thousands separators, for result counts.
 *
 * @param value - The number to format.
 * @returns The formatted number, e.g. `1,284`.
 */
export function formatCount(value: number): string {
  return new Intl.NumberFormat(DISPLAY_LOCALE).format(value)
}
