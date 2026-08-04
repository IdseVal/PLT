// @vitest-environment node
/**
 * Invariants of the generated map geometry.
 *
 * `src/components/map/geometry.generated.ts` is written by
 * `scripts/generate-map-geometry.mjs` from Natural Earth data. Nobody reviews eighty
 * kilobytes of path data by eye, so the properties the map depends on are asserted here
 * instead: that the coverage is exactly Annex 2 of the core document, that every shape is
 * inside the frame, and that the EU marker really does sit in open sea.
 *
 * The Annex 2 list is restated here rather than imported from the geometry, so that a
 * jurisdiction quietly added to or dropped from the generator fails this test instead of
 * silently redefining what the tracker claims to cover.
 */

import { describe, expect, it } from 'vitest'

import {
  CONTEXT_PATH,
  EU_MARKER,
  JURISDICTION_SHAPES,
  MAP_VIEW_BOX,
} from '@/components/map/geometry.generated'

/**
 * The jurisdictions of `docs/core-document.md` Annex 2, by `map_feature_id` and name.
 *
 * Annex 2 lists a source for twenty-six member states. Sweden has no row in it, so it is
 * absent here too: the map draws the coverage the project documents, and closing that gap is
 * a change to the core document rather than to this file.
 */
const ANNEX_2: readonly (readonly [string, string])[] = [
  ['AT', 'Austria'],
  ['BE', 'Belgium'],
  ['BG', 'Bulgaria'],
  ['CY', 'Cyprus'],
  ['CZ', 'Czechia'],
  ['DE', 'Germany'],
  ['DK', 'Denmark'],
  ['EE', 'Estonia'],
  ['ES', 'Spain'],
  ['FI', 'Finland'],
  ['FR', 'France'],
  ['GR', 'Greece'],
  ['HR', 'Croatia'],
  ['HU', 'Hungary'],
  ['IE', 'Ireland'],
  ['IT', 'Italy'],
  ['LT', 'Lithuania'],
  ['LU', 'Luxembourg'],
  ['LV', 'Latvia'],
  ['MT', 'Malta'],
  ['NL', 'Netherlands'],
  ['PL', 'Poland'],
  ['PT', 'Portugal'],
  ['RO', 'Romania'],
  ['SI', 'Slovenia'],
  ['SK', 'Slovakia'],
]

/**
 * Every coordinate pair in an SVG path built by the generator.
 *
 * The generator emits nothing but `M x y L x y … Z`, so the numbers can be read straight out
 * without a path parser.
 *
 * @param path - The `d` attribute.
 * @returns The points.
 */
function pointsOf(path: string): readonly (readonly [number, number])[] {
  return [...path.matchAll(/(-?\d+(?:\.\d+)?)\s(-?\d+(?:\.\d+)?)/g)].map(
    (match) => [Number(match[1]), Number(match[2])] as const,
  )
}

describe('map geometry', () => {
  it('covers exactly the jurisdictions of Annex 2', () => {
    expect(JURISDICTION_SHAPES.map((shape) => [shape.code, shape.name])).toEqual(
      ANNEX_2.map(([code, name]) => [code, name]),
    )
  })

  it('gives every jurisdiction a drawable shape', () => {
    for (const shape of JURISDICTION_SHAPES) {
      expect(shape.path.startsWith('M'), `${shape.code} starts a path`).toBe(true)
      expect(shape.path.endsWith('Z'), `${shape.code} closes its path`).toBe(true)
      expect(pointsOf(shape.path).length, `${shape.code} has points`).toBeGreaterThan(2)
    }
  })

  it('keeps every shape inside the frame', () => {
    for (const shape of [...JURISDICTION_SHAPES.map((entry) => entry.path), CONTEXT_PATH]) {
      for (const [x, y] of pointsOf(shape)) {
        expect(x).toBeGreaterThanOrEqual(0)
        expect(x).toBeLessThanOrEqual(MAP_VIEW_BOX.width)
        expect(y).toBeGreaterThanOrEqual(0)
        expect(y).toBeLessThanOrEqual(MAP_VIEW_BOX.height)
      }
    }
  })

  it('anchors every tooltip inside the frame', () => {
    for (const shape of JURISDICTION_SHAPES) {
      expect(shape.labelPoint.x).toBeGreaterThan(0)
      expect(shape.labelPoint.x).toBeLessThan(MAP_VIEW_BOX.width)
      expect(shape.labelPoint.y).toBeGreaterThan(0)
      expect(shape.labelPoint.y).toBeLessThan(MAP_VIEW_BOX.height)
    }
  })

  it('gives a pointer target to the jurisdictions too small to hit, and only to those', () => {
    expect(JURISDICTION_SHAPES.filter((shape) => shape.needsMarker).map((shape) => shape.code)).toEqual([
      'LU',
      'MT',
    ])
  })

  it('puts the EU marker in open sea, clear of every coastline', () => {
    const land = [...JURISDICTION_SHAPES.map((shape) => shape.path), CONTEXT_PATH].flatMap(pointsOf)
    const nearest = land.reduce(
      (best, [x, y]) => Math.min(best, Math.hypot(x - EU_MARKER.x, y - EU_MARKER.y)),
      Number.POSITIVE_INFINITY,
    )

    expect(nearest).toBeGreaterThan(EU_MARKER.radius)
  })

  it('puts the EU marker in the North Sea rather than anywhere else that is wet', () => {
    // North of the Netherlands and west of Denmark, which are the two jurisdictions that
    // frame that stretch of sea. Both are drawn, so this cannot drift without failing.
    const netherlands = JURISDICTION_SHAPES.find((shape) => shape.code === 'NL')
    const denmark = JURISDICTION_SHAPES.find((shape) => shape.code === 'DK')

    expect(netherlands).toBeDefined()
    expect(denmark).toBeDefined()
    expect(EU_MARKER.y).toBeLessThan(netherlands?.labelPoint.y ?? 0)
    expect(EU_MARKER.x).toBeLessThan(denmark?.labelPoint.x ?? 0)
  })
})
