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

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import {
  CONTEXT_PATH,
  EU_MARKER,
  JURISDICTION_SHAPES,
  MAP_VIEW_BOX,
} from '@/components/map/geometry.generated'

/** The document that decides the coverage. Vitest runs with `frontend/` as its directory. */
const ANNEX_PATH = resolve(process.cwd(), '..', 'docs', 'core-document.md')

/** Heading the jurisdiction table sits under. */
const ANNEX_HEADING = '## Annex 2: project data sources'

/** The Union's row: a jurisdiction, but drawn as a mark rather than as a shape. */
const EU_JURISDICTION = 'EU'

/**
 * The jurisdictions Annex 2 lists, read from the document itself.
 *
 * Read rather than restated, and the reason is the whole point of this file. The coverage used
 * to be written down in three places and read from none, so when Annex 2 gained Sweden the map
 * stayed a member state short of the document while every check passed. The committed geometry
 * is now compared against the annex on every test run, which needs no network and so runs in
 * CI, where the generator's own `--check` does not.
 *
 * @returns Jurisdiction names in the order the annex lists them, the Union excluded.
 */
function annexMemberStates(): readonly string[] {
  const document = readFileSync(ANNEX_PATH, 'utf8')
  const start = document.indexOf(ANNEX_HEADING)
  expect(start, `${ANNEX_PATH} has no "${ANNEX_HEADING}" heading`).toBeGreaterThan(-1)

  const names: string[] = []
  for (const line of document.slice(start + ANNEX_HEADING.length).split('\n')) {
    if (line.startsWith('#')) break
    if (!line.startsWith('|')) continue

    const first = line.split('|')[1]?.trim() ?? ''
    if (first === '' || first === 'Jurisdiction' || /^-+$/.test(first)) continue
    if (!names.includes(first)) names.push(first)
  }

  expect(names, 'Annex 2 must list the Union').toContain(EU_JURISDICTION)
  return names.filter((name) => name !== EU_JURISDICTION)
}

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
  it('covers exactly the member states Annex 2 lists', () => {
    expect([...JURISDICTION_SHAPES.map((shape) => shape.name)].sort()).toEqual(
      [...annexMemberStates()].sort(),
    )
  })

  it('identifies every jurisdiction by a distinct ISO 3166-1 alpha-2 code', () => {
    const codes = JURISDICTION_SHAPES.map((shape) => shape.code)

    for (const code of codes) expect(code).toMatch(/^[A-Z]{2}$/)
    expect(new Set(codes).size).toBe(codes.length)
    // The map resolves a jurisdiction against `map_feature_id`, so a code that drifted from the
    // country it draws would put a count on the wrong shape. Two spot checks, written out.
    expect(JURISDICTION_SHAPES.find((shape) => shape.name === 'Netherlands')?.code).toBe('NL')
    expect(JURISDICTION_SHAPES.find((shape) => shape.name === 'Sweden')?.code).toBe('SE')
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
