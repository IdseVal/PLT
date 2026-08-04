// @vitest-environment node
/**
 * Invariants of the generated map geometry.
 *
 * `src/components/map/geometry.generated.ts` is written by
 * `scripts/generate-map-geometry.mjs` from Natural Earth data. Nobody reviews eighty
 * kilobytes of path data by eye, so the properties the map depends on are asserted here
 * instead: that the coverage is exactly Annex 2 of the core document, that every shape
 * carries the ISO code of the country it draws, that every shape is inside the frame, and
 * that the EU marker really does sit in open sea.
 *
 * Two things the map must get right, and neither is checked by reading the geometry:
 *
 * - **which** jurisdictions are drawn. That is decided by Annex 2, so {@link annexMemberStates}
 *   reads the annex rather than restating it: a jurisdiction added to or dropped from the
 *   generator fails here instead of silently redefining what the tracker claims to cover.
 * - **under which code** each one is drawn. Annex 2 names jurisdictions and carries no ISO
 *   codes, so it cannot guard that half, and the mapping lives only in the generator. It is
 *   therefore written out again in {@link JURISDICTION_CODES}, by hand, so that a code that
 *   drifted from the country it draws fails by name.
 *
 * Both statements are independent of the asset they check. Neither is derived from it.
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
 * The ISO 3166-1 alpha-2 code each Annex 2 jurisdiction must be drawn under.
 *
 * The map resolves a jurisdiction against `map_feature_id` (`docs/architecture.md` section 3),
 * which is this code: it carries the case count into the tooltip and the shape's link into
 * `/cases?jurisdiction=<code>`. Two codes exchanged between two countries leaves every shape
 * well-formed and every count plausible, and puts one member state's litigation on another's
 * outline — wrong data, confidently presented, on the signature element of the tracker.
 *
 * Annex 2 lists names and no codes, so the pairing exists nowhere but in the generator's
 * `GEOMETRY_BY_JURISDICTION`, and reading the generated asset to check the asset would only
 * agree with itself. This table is therefore written out by hand: the one part of the map's
 * definition the annex cannot express, and the only part restated here.
 *
 * Adding a member state to Annex 2 is meant to require a deliberate line here. That is the
 * cost of the guard, and it is one line.
 */
const JURISDICTION_CODES: Readonly<Record<string, string>> = {
  Austria: 'AT',
  Belgium: 'BE',
  Bulgaria: 'BG',
  Croatia: 'HR',
  Cyprus: 'CY',
  Czechia: 'CZ',
  Denmark: 'DK',
  Estonia: 'EE',
  Finland: 'FI',
  France: 'FR',
  Germany: 'DE',
  Greece: 'GR',
  Hungary: 'HU',
  Ireland: 'IE',
  Italy: 'IT',
  Latvia: 'LV',
  Lithuania: 'LT',
  Luxembourg: 'LU',
  Malta: 'MT',
  Netherlands: 'NL',
  Poland: 'PL',
  Portugal: 'PT',
  Romania: 'RO',
  Slovakia: 'SK',
  Slovenia: 'SI',
  Spain: 'ES',
  Sweden: 'SE',
}

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

  it('knows a code for every member state Annex 2 lists, and for no one else', () => {
    // Keyed on the annex rather than on the geometry, so this table cannot quietly fall behind
    // the document either: a jurisdiction added to Annex 2 fails here until a code is written.
    expect(Object.keys(JURISDICTION_CODES).sort()).toEqual([...annexMemberStates()].sort())
  })

  it('pins codes that ISO 3166-1 itself agrees name those countries', () => {
    // The table above is only as good as the hand that wrote it, and the tempting way to fix a
    // failure below is to edit it until it matches the asset. So it is checked against an
    // authority outside this repository: the ICU region names Node ships with. A transposed or
    // mistyped code names the wrong country, or no country, and fails here.
    const isoName = new Intl.DisplayNames(['en'], { type: 'region' })

    for (const [name, code] of Object.entries(JURISDICTION_CODES)) {
      expect(code, `${name} needs an alpha-2 code`).toMatch(/^[A-Z]{2}$/)
      expect(isoName.of(code), `ISO 3166-1 reads ${code} as`).toBe(name)
    }
  })

  it('draws every jurisdiction under its own ISO 3166-1 alpha-2 code', () => {
    for (const shape of JURISDICTION_SHAPES) {
      expect(shape.code, `${shape.name} is drawn under`).toBe(JURISDICTION_CODES[shape.name])
    }

    const codes = JURISDICTION_SHAPES.map((shape) => shape.code)
    expect(new Set(codes).size, 'no two shapes share a code').toBe(codes.length)
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
