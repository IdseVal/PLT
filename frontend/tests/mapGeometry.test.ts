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
 * Three things the map must get right, and none of them is checked by reading the geometry:
 *
 * - **which** jurisdictions are drawn. That is decided by Annex 2, so {@link annexMemberStates}
 *   reads the annex rather than restating it: a jurisdiction added to or dropped from the
 *   generator fails here instead of silently redefining what the tracker claims to cover.
 * - **under which code** each one is drawn. Annex 2 names jurisdictions and carries no ISO
 *   codes, so it cannot guard that half, and the mapping lives only in the generator. It is
 *   therefore written out again in {@link JURISDICTION_CODES}, by hand, so that a code that
 *   drifted from the country it draws fails by name.
 * - **whose outline** each one is drawn from. Annex 2 carries no geometry either, so the
 *   jurisdiction-to-Natural-Earth-id half of that same table is equally unguarded, and two ids
 *   exchanged there draw Belgium's outline labelled Austria, with Austria's code and Austria's
 *   case count: a plausible map of the wrong country, which no amount of reading the path data
 *   can detect. {@link JURISDICTION_LOCATIONS} says where on the globe each country is, and
 *   {@link GEOMETRY_BY_JURISDICTION}'s ids are put to ISO 3166-1 itself.
 *
 * Every statement here is independent of the asset it checks. None is derived from it.
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import {
  CONTEXT_PATH,
  EU_MARKER,
  JURISDICTION_ANCHORS,
  JURISDICTION_SHAPES,
  MAP_VIEW_BOX,
} from '@/components/map/geometry.generated'

import { GEOMETRY_BY_JURISDICTION } from '../scripts/generate-map-geometry.mjs'

/** The document that decides the coverage. Vitest runs with `frontend/` as its directory. */
const ANNEX_PATH = resolve(process.cwd(), '..', 'docs', 'CORE_DOCUMENT.md')

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

/** A position on the globe, in degrees east of Greenwich and north of the equator. */
interface Location {
  readonly lon: number
  readonly lat: number
}

/**
 * Roughly where each Annex 2 jurisdiction is on the globe.
 *
 * The generator picks each country's outline out of Natural Earth by numeric id, and that id
 * is the last thing about the map nothing outside the generator states. Exchange two of them
 * and the map draws one member state's coastline under its neighbour's name, code, colour and
 * case count — every shape well-formed, every count plausible, and a reader taking Belgian
 * litigation volume for Austrian with nothing on the page to contradict them.
 *
 * These are hand-written from ordinary geographic knowledge of where these countries are, in
 * **unprojected degrees**. That is the point of them: a position on the Earth is independent
 * of how the map is drawn, so this table stays true when the projection is retuned, the frame
 * is moved or the simplification is loosened — none of which change where Austria is. A pin in
 * SVG user units would be a statement about the current projection constants instead, and
 * would say nothing about which country had been drawn.
 *
 * **Precision is not the point and is not claimed.** What is being caught is a whole country
 * swapped for another, so a rough centre is ample: every pin is within a degree of the geometry
 * it belongs to, and the closest two countries here, Belgium and Luxembourg, are a degree and a
 * quarter apart. Coordinates written to a tenth keep this table something a
 * reviewer can check against an atlas, or their memory, rather than against the asset.
 */
const JURISDICTION_LOCATIONS: Readonly<Record<string, Location>> = {
  Austria: { lon: 14.1, lat: 47.6 },
  Belgium: { lon: 4.6, lat: 50.6 },
  Bulgaria: { lon: 25.2, lat: 42.8 },
  Croatia: { lon: 16.4, lat: 45.1 },
  Cyprus: { lon: 33.0, lat: 35.0 },
  Czechia: { lon: 15.3, lat: 49.8 },
  Denmark: { lon: 9.4, lat: 56.1 },
  Estonia: { lon: 25.6, lat: 58.7 },
  Finland: { lon: 26.5, lat: 64.5 },
  France: { lon: 2.4, lat: 46.7 },
  Germany: { lon: 10.3, lat: 51.1 },
  Greece: { lon: 22.0, lat: 39.3 },
  Hungary: { lon: 19.4, lat: 47.2 },
  Ireland: { lon: -8.1, lat: 53.2 },
  Italy: { lon: 12.6, lat: 42.9 },
  Latvia: { lon: 24.9, lat: 56.9 },
  Lithuania: { lon: 23.9, lat: 55.3 },
  Luxembourg: { lon: 6.1, lat: 49.8 },
  Malta: { lon: 14.4, lat: 35.9 },
  Netherlands: { lon: 5.6, lat: 52.2 },
  Poland: { lon: 19.4, lat: 52.1 },
  Portugal: { lon: -8.1, lat: 39.6 },
  Romania: { lon: 25.0, lat: 45.9 },
  Slovakia: { lon: 19.5, lat: 48.7 },
  Slovenia: { lon: 14.8, lat: 46.1 },
  Spain: { lon: -3.6, lat: 40.3 },
  Sweden: { lon: 16.5, lat: 62.8 },
}

/**
 * How far a shape's anchor may sit from its pinned location, in degrees of latitude — one of
 * which is about 111 km.
 *
 * Generous on purpose. The worst pin above is two thirds of a degree from the geometry it
 * belongs to (Italy: the mainland's area centroid sits north of where a hand puts the country,
 * because the peninsula tapers), and Natural Earth revises its coastlines between releases, so
 * a tolerance that tracked the current asset closely would fail on a data update and teach the
 * next reader to loosen it. Two degrees will never do that, and no country in Annex 2 is two
 * degrees from where it belongs.
 *
 * A tolerance this generous cannot by itself separate the closest neighbours — Brussels to
 * Luxembourg is well inside it — which is why the transposition is caught by comparing every
 * pin, not by this bound alone.
 */
const MAX_ANCHOR_OFFSET = 2

/**
 * Rough separation of two positions, in degrees of latitude.
 *
 * Longitude is scaled by the cosine of the latitude, so a degree means about the same distance
 * on the ground in Lapland as in Cyprus. Plane trigonometry over a few hundred kilometres is
 * accurate to well within the tolerances used here; nothing on this map needs a geodesic.
 *
 * @param a - One position.
 * @param b - The other.
 * @returns Their separation, in degrees of latitude.
 */
function separation(a: Location, b: Location): number {
  const meanLatitude = (((a.lat + b.lat) / 2) * Math.PI) / 180
  return Math.hypot(a.lat - b.lat, (a.lon - b.lon) * Math.cos(meanLatitude))
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

/**
 * The rings of a generated path, largest first.
 *
 * @param path - The `d` attribute.
 * @returns One point list per `M … Z` subpath, ordered by descending area.
 */
function ringsOf(path: string): readonly (readonly (readonly [number, number])[])[] {
  return path
    .split('M')
    .filter((ring) => ring.length > 0)
    .map(pointsOf)
    .sort((a, b) => Math.abs(doubleArea(b)) - Math.abs(doubleArea(a)))
}

/**
 * Twice the signed area of a ring, by the shoelace formula.
 *
 * @param ring - The ring, in user units.
 * @returns Twice its signed area.
 */
function doubleArea(ring: readonly (readonly [number, number])[]): number {
  return ring.reduce((total, [x1, y1], index) => {
    const [x2, y2] = ring[(index + 1) % ring.length] ?? [x1, y1]
    return total + x1 * y2 - x2 * y1
  }, 0)
}

/**
 * Centroid of a ring.
 *
 * @param ring - The ring, in user units.
 * @returns Its area centroid.
 */
function ringCentroid(ring: readonly (readonly [number, number])[]): readonly [number, number] {
  const area = doubleArea(ring)
  let x = 0
  let y = 0
  for (const [index, [x1, y1]] of ring.entries()) {
    const [x2, y2] = ring[(index + 1) % ring.length] ?? [x1, y1]
    const cross = x1 * y2 - x2 * y1
    x += (x1 + x2) * cross
    y += (y1 + y2) * cross
  }
  return [x / (3 * area), y / (3 * area)]
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

  it('takes every outline from the Natural Earth id ISO 3166-1 gives that country', () => {
    // The id the generator selects geometry by is the country's ISO 3166-1 numeric code, and
    // CLDR — the region data Node ships, the same authority the codes above are held to —
    // carries the numeric-to-alpha-2 aliases, so `und-040` canonicalises to `und-AT`. Two ids
    // transposed therefore fail here as well as geographically, and this failure cannot be
    // argued away by editing a pin: nothing in this repository decides what 040 means.
    expect(Object.keys(GEOMETRY_BY_JURISDICTION).sort()).toEqual([...annexMemberStates()].sort())

    for (const [name, entry] of Object.entries(GEOMETRY_BY_JURISDICTION)) {
      expect(entry.source, `${name} needs a three-digit ISO 3166-1 numeric code`).toMatch(/^\d{3}$/)
      expect(new Intl.Locale(`und-${entry.source}`).region, `ISO 3166-1 reads ${entry.source} as`).toBe(
        JURISDICTION_CODES[name],
      )
    }

    const ids = Object.values(GEOMETRY_BY_JURISDICTION).map((entry) => entry.source)
    expect(new Set(ids).size, 'no two jurisdictions are drawn from one country').toBe(ids.length)
  })

  it('knows where every member state Annex 2 lists is, and where no one else is', () => {
    // Keyed on the annex like the code table, so a member state added to the document has to be
    // placed on the globe before its shape is accepted.
    expect(Object.keys(JURISDICTION_LOCATIONS).sort()).toEqual([...annexMemberStates()].sort())
  })

  it('draws every jurisdiction from geometry that lies where that country is', () => {
    for (const shape of JURISDICTION_SHAPES) {
      const pin = JURISDICTION_LOCATIONS[shape.name]
      const anchor = JURISDICTION_ANCHORS[shape.code]
      expect(pin, `${shape.name} needs a pinned location`).toBeDefined()
      expect(anchor, `${shape.code} needs a geographic anchor`).toBeDefined()
      if (pin === undefined || anchor === undefined) continue

      const offset = separation(anchor, pin)
      expect(
        offset,
        `${shape.name} is drawn from geometry centred at ${anchor.lat}°N ${anchor.lon}°E, ` +
          `but ${shape.name} is at about ${pin.lat}°N ${pin.lon}°E`,
      ).toBeLessThan(MAX_ANCHOR_OFFSET)
    }
  })

  it("draws no jurisdiction from a neighbour's outline", () => {
    // The bound above is deliberately loose, and a country's nearest neighbour can sit well
    // inside it, so the transposition that matters — two adjacent member states exchanged — is
    // caught by asking a sharper question: of all twenty-seven places Annex 2 covers, is the
    // outline drawn for this one nearest to its own?
    for (const shape of JURISDICTION_SHAPES) {
      const anchor = JURISDICTION_ANCHORS[shape.code]
      if (anchor === undefined) continue

      const nearest = Object.entries(JURISDICTION_LOCATIONS).reduce((best, candidate) =>
        separation(anchor, candidate[1]) < separation(anchor, best[1]) ? candidate : best,
      )

      expect(nearest[0], `the outline drawn for ${shape.name} is closest to`).toBe(shape.name)
    }
  })

  it('labels every shape at the centre of that very shape', () => {
    // The geographic pins above catch an outline taken from the wrong country, because the
    // anchor is measured from the geometry and moves with it. They cannot catch the geometry
    // being moved afterwards: paths exchanged between two entries in the generated file leave
    // the anchors, the names and the codes all correct. This does catch that, without knowing
    // anything about the projection — a label point is the centroid of the largest ring of the
    // path it belongs to, so a shape that has been given someone else's outline is labelled
    // hundreds of user units away from itself.
    for (const shape of JURISDICTION_SHAPES) {
      const largest = ringsOf(shape.path)[0]
      expect(largest, `${shape.code} has a ring`).toBeDefined()
      if (largest === undefined) continue

      const [x, y] = ringCentroid(largest)
      const drift = Math.hypot(x - shape.labelPoint.x, y - shape.labelPoint.y)
      expect(
        drift,
        `${shape.name} is labelled at (${shape.labelPoint.x}, ${shape.labelPoint.y}) ` +
          `but the shape it labels is centred at (${x.toFixed(1)}, ${y.toFixed(1)})`,
      ).toBeLessThan(1)
    }
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
