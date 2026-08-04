/**
 * How a case count becomes a colour, and how it is put into words.
 *
 * Counts across jurisdictions differ by orders of magnitude — at launch the Netherlands and
 * the European Union hold cases and every other jurisdiction holds none — so the shading is
 * banded on a logarithmic step rather than scaled linearly, which would otherwise render
 * everything but the largest jurisdiction in the same near-empty tint.
 *
 * The bands are drawn with the four accent tokens of `tailwind.config.js`, in increasing
 * darkness, and nothing here names a colour: the Wageningen Law styling package has to land
 * as a one-file change (`CONTRIBUTING.md` section 5).
 *
 * Colour is never the only carrier of the count. Every shape's accessible name states the
 * count in words, the tooltip prints it, and the legend spells the bands out.
 */

/** Shading bands, from "nothing yet" to the largest collections. */
export type CountBand = 'none' | 'low' | 'medium' | 'high'

/** Lower bound of the `low` band: the first case a jurisdiction has. */
const LOW_THRESHOLD = 1

/** Lower bound of the `medium` band. */
const MEDIUM_THRESHOLD = 10

/** Lower bound of the `high` band. */
const HIGH_THRESHOLD = 100

/**
 * Band a case count falls in.
 *
 * @param count - The count, or `null` when it is not known (still loading, or the request
 *   failed). An unknown count is shaded as `none`: the map draws the jurisdiction, it just
 *   makes no claim about it.
 * @returns The band.
 */
export function bandOf(count: number | null): CountBand {
  if (count === null || count < LOW_THRESHOLD) return 'none'
  if (count < MEDIUM_THRESHOLD) return 'low'
  if (count < HIGH_THRESHOLD) return 'medium'
  return 'high'
}

/** Fill class per band, for an SVG shape. */
export const BAND_FILL: Readonly<Record<CountBand, string>> = {
  none: 'fill-plt-accent-soft',
  low: 'fill-plt-accent',
  medium: 'fill-plt-accent-strong',
  high: 'fill-plt-accent-deep',
}

/** Background class per band, for the legend's swatches. */
export const BAND_SWATCH: Readonly<Record<CountBand, string>> = {
  none: 'bg-plt-accent-soft',
  low: 'bg-plt-accent',
  medium: 'bg-plt-accent-strong',
  high: 'bg-plt-accent-deep',
}

/** What each band means, in the legend's order. */
export const BAND_LEGEND: readonly { readonly band: CountBand; readonly label: string }[] = [
  { band: 'none', label: 'No cases yet' },
  { band: 'low', label: `${LOW_THRESHOLD}–${MEDIUM_THRESHOLD - 1} cases` },
  { band: 'medium', label: `${MEDIUM_THRESHOLD}–${HIGH_THRESHOLD - 1} cases` },
  { band: 'high', label: `${HIGH_THRESHOLD} cases or more` },
]

/**
 * The count as a reader meets it, in the tooltip and in the shape's accessible name.
 *
 * @param count - The count, or `null` when it is not known.
 * @returns A phrase: `no cases yet`, `1 case`, `1,204 cases`, or `case count unavailable`.
 */
export function countPhrase(count: number | null): string {
  if (count === null) return 'case count unavailable'
  if (count === 0) return 'no cases yet'
  if (count === 1) return '1 case'
  return `${count.toLocaleString('en-GB')} cases`
}
