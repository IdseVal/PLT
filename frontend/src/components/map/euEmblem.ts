/**
 * The circle of twelve stars, as an SVG path.
 *
 * The European Union is a jurisdiction of its own on this map rather than the sum of its
 * member states (`docs/CORE_DOCUMENT.md` section 3.3), so it needs a mark of its own: a disc
 * in the North Sea carrying the emblem, hoverable and clickable exactly like a country.
 *
 * The proportions are those of the emblem itself — the stars sit on a circle of two thirds
 * the disc's radius, and each star is circumscribed by a circle of one ninth of it — but the
 * colours are the theme's, not the emblem's official blue and gold, because every colour in
 * this application has to come from `tailwind.config.js` so the Wageningen Law styling
 * package can land as a one-file change (`CONTRIBUTING.md` section 5). Replacing this with
 * the official asset is a job for that package.
 */

/** Points on a five-pointed star: five outer, five inner, alternating. */
const STAR_POINTS = 10

/** Ratio of a five-pointed star's inner radius to its outer radius. */
const STAR_INNER_RATIO = 0.382

/** Stars in the circle. Twelve, and never a count of anything. */
const STAR_COUNT = 12

/** Radius of the circle the stars sit on, as a fraction of the disc's radius. */
const RING_RATIO = 2 / 3

/** Radius circumscribing one star, as a fraction of the disc's radius. */
const STAR_RATIO = 1 / 9

/** Straight up, in the radian convention of SVG's coordinate system. */
const UP = -Math.PI / 2

/**
 * Round a coordinate to two decimals, so the emitted path stays short.
 *
 * @param value - The coordinate.
 * @returns Its shortest decimal form.
 */
function round(value: number): string {
  return String(Math.round(value * 100) / 100)
}

/**
 * One five-pointed star, pointing up.
 *
 * @param centreX - Centre, x.
 * @param centreY - Centre, y.
 * @param radius - Circumscribed radius.
 * @returns A closed sub-path.
 */
function star(centreX: number, centreY: number, radius: number): string {
  const points: string[] = []
  for (let index = 0; index < STAR_POINTS; index += 1) {
    const reach = index % 2 === 0 ? radius : radius * STAR_INNER_RATIO
    const angle = UP + (index * Math.PI) / (STAR_POINTS / 2)
    points.push(`${round(centreX + reach * Math.cos(angle))} ${round(centreY + reach * Math.sin(angle))}`)
  }
  return `M${points.join('L')}Z`
}

/**
 * The twelve stars of the emblem, as one path.
 *
 * @param centreX - Centre of the disc, x.
 * @param centreY - Centre of the disc, y.
 * @param radius - Radius of the disc.
 * @returns The `d` attribute of the star circle.
 */
export function euStarsPath(centreX: number, centreY: number, radius: number): string {
  const ring = radius * RING_RATIO
  const size = radius * STAR_RATIO
  return Array.from({ length: STAR_COUNT }, (_unused, index) => {
    const angle = UP + (index * 2 * Math.PI) / STAR_COUNT
    return star(centreX + ring * Math.cos(angle), centreY + ring * Math.sin(angle), size)
  }).join('')
}
