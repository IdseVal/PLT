/**
 * Generate `src/components/map/geometry.generated.ts` — the map's geometry asset.
 *
 * The map ships as **pre-projected inline SVG**: this script does the cartography once, at
 * development time, and writes out plain SVG path strings. The browser therefore downloads
 * no mapping library, runs no projection, and makes no request to a tile server, which is
 * what `docs/architecture.md` section 6 and issue #13 require (the tracker has to work
 * offline and load fast).
 *
 * ## Running it
 *
 * ```bash
 * cd frontend
 * node scripts/generate-map-geometry.mjs            # writes the module
 * node scripts/generate-map-geometry.mjs --check    # fails if the module is out of date
 * ```
 *
 * It has **no dependencies**: the TopoJSON decoder, the Lambert conformal conic projection,
 * the polygon clipper and the line simplifier are all implemented below, in a few dozen
 * lines each. That keeps a build-time-only concern out of `package.json` entirely.
 *
 * ## Source data
 *
 * Natural Earth 1:50m admin-0 countries, as redistributed in TopoJSON form by
 * `world-atlas@2.0.2` (public domain). Downloaded once to `--cache` (default
 * `node_modules/.cache/plt-map`) so a re-run is offline. The upstream URL is only ever
 * contacted by this script, never by the application.
 *
 * ## What comes out
 *
 * - one path per jurisdiction in `docs/core-document.md` Annex 2, keyed by its
 *   `map_feature_id` (`docs/architecture.md` section 3: the ISO 3166-1 alpha-2 code). **The
 *   annex is read, not restated**: the table in that document decides the coverage, and a
 *   member state added to it that this script cannot place stops the run by name rather than
 *   going quietly missing from the map;
 * - one merged path for the surrounding countries, which are context, not jurisdictions;
 * - a label point per jurisdiction, used to anchor the tooltip on keyboard focus;
 * - a geographic anchor per jurisdiction: where on the globe, in unprojected lon/lat, the
 *   outline that was drawn actually lies. Nothing renders it; it is what lets the tests say
 *   that the shape labelled Austria is the country at 47°N 14°E and not its neighbour;
 * - a marker flag for jurisdictions too small to hit with a pointer (Luxembourg, Malta);
 * - the position of the EU marker in the North Sea, with the clearance measured against
 *   every rendered coastline so it cannot silently drift onto land.
 */

import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const FRONTEND_ROOT = resolve(HERE, '..')
const OUTPUT_PATH = join(FRONTEND_ROOT, 'src', 'components', 'map', 'geometry.generated.ts')

/** Source geometry: Natural Earth 1:50m via world-atlas, pinned. */
const SOURCE_URL = 'https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-50m.json'
const SOURCE_FILE = 'countries-50m.json'

/**
 * The window the map shows. Its projection is what the `viewBox` is fitted to.
 *
 * North stops just above the northern tip of Finland and south just below Crete and Malta,
 * so every Annex 2 jurisdiction is inside the frame with nothing to spare: the map is a tall
 * shape already, and empty sea costs height the home-page layout would rather spend
 * elsewhere.
 */
const VIEW = { west: -11.5, east: 35.5, south: 34, north: 70.8 }

/**
 * A generous geographic pre-filter, applied before projection purely to keep the working set
 * small. The cut it makes is far outside the frame; the visible edges are made afterwards, by
 * clipping in canvas space, which is what stops a straight cut in lon/lat from projecting
 * into a slanted wedge across the corner of the map.
 */
const CULL = { west: -45, east: 65, south: 5, north: 88 }

/**
 * Lambert conformal conic, the usual choice for a mid-latitude regional map: it keeps
 * country shapes recognisable across the whole of Europe, where a Mercator would stretch
 * Finland to twice its size.
 */
const PROJECTION = { parallel1: 40, parallel2: 62, referenceLongitude: 12, referenceLatitude: 52 }

/** Width of the generated `viewBox`, in SVG user units. Height follows from the projection. */
const CANVAS_WIDTH = 820

/** Douglas–Peucker tolerance, in SVG user units. Half a unit is invisible at any real size. */
const SIMPLIFY_TOLERANCE = 0.45

/** Rings smaller than this (square user units) are dropped: islands too small to see. */
const MIN_RING_AREA = 1.2

/** A jurisdiction whose largest ring is smaller than this gets a pointer target of its own. */
const MARKER_AREA_THRESHOLD = 90

/**
 * Radius of that pointer target, in **CSS pixels**: the component scales it into user units
 * from the map's rendered width, so a country too small to hit keeps the same target on a
 * phone as on a desktop instead of shrinking with the map.
 */
const MARKER_RADIUS = 9

/** Where the EU marker sits, in the North Sea, and how big it is. */
const EU_MARKER = { longitude: 2.6, latitude: 56.4, radius: 26 }

/** Smallest acceptable gap between the EU marker and any rendered coastline, in user units. */
const MIN_EU_CLEARANCE = 3

/** The document that decides which jurisdictions the map covers. */
const ANNEX_PATH = resolve(FRONTEND_ROOT, '..', 'docs', 'core-document.md')

/** Heading the jurisdiction table sits under. */
const ANNEX_HEADING = '## Annex 2: project data sources'

/** The Union's row in that table. It is a jurisdiction, but a marker rather than a shape. */
const EU_JURISDICTION = 'EU'

/**
 * How to draw a jurisdiction Annex 2 names.
 *
 * **This table does not decide the coverage** — Annex 2 does, and {@link readAnnexJurisdictions}
 * reads it. This says only how to render a jurisdiction the annex already lists: its ISO
 * 3166-1 alpha-2 code, which is the `map_feature_id` the API resolves against
 * (`docs/architecture.md` section 3), and its ISO 3166-1 numeric id in Natural Earth, which is
 * stable across releases so an upstream rename cannot silently drop a country.
 *
 * A member state added to the annex and not to this table stops the generator with a message
 * naming it. That is the point: the map was a member state short of the annex for a while, and
 * every check passed, because the coverage was written down in three places and read from none.
 *
 * Neither column below is the last word on itself, and for the same reason: Annex 2 carries
 * names only, so it cannot catch a value exchanged between two countries here. A transposed
 * `code` draws one member state's case count and `/cases` link on another's outline; a
 * transposed `source` draws another country's *outline* under this one's name. Both leave every
 * shape well-formed and every count plausible. `tests/mapGeometry.test.ts` therefore checks this
 * table from outside: the name-to-code pairing is restated there by hand, and both columns are
 * put to ISO 3166-1 itself through the region data Node ships — `source` is the country's ISO
 * numeric code, which ICU resolves to an alpha-2 code, which must be the `code` beside it.
 * That is a check no edit to this file can satisfy except a correct one.
 */
export const GEOMETRY_BY_JURISDICTION = {
  Austria: { code: 'AT', source: '040' },
  Belgium: { code: 'BE', source: '056' },
  Bulgaria: { code: 'BG', source: '100' },
  Croatia: { code: 'HR', source: '191' },
  Cyprus: { code: 'CY', source: '196' },
  Czechia: { code: 'CZ', source: '203' },
  Denmark: { code: 'DK', source: '208' },
  Estonia: { code: 'EE', source: '233' },
  Finland: { code: 'FI', source: '246' },
  France: { code: 'FR', source: '250' },
  Germany: { code: 'DE', source: '276' },
  Greece: { code: 'GR', source: '300' },
  Hungary: { code: 'HU', source: '348' },
  Ireland: { code: 'IE', source: '372' },
  Italy: { code: 'IT', source: '380' },
  Latvia: { code: 'LV', source: '428' },
  Lithuania: { code: 'LT', source: '440' },
  Luxembourg: { code: 'LU', source: '442' },
  Malta: { code: 'MT', source: '470' },
  Netherlands: { code: 'NL', source: '528' },
  Poland: { code: 'PL', source: '616' },
  Portugal: { code: 'PT', source: '620' },
  Romania: { code: 'RO', source: '642' },
  Slovakia: { code: 'SK', source: '703' },
  Slovenia: { code: 'SI', source: '705' },
  Spain: { code: 'ES', source: '724' },
  Sweden: { code: 'SE', source: '752' },
}

/**
 * The jurisdictions Annex 2 lists, in the order it lists them.
 *
 * Annex 2 is a Markdown table whose first column is the jurisdiction and whose rows repeat it
 * once per source, so the reading is: take the rows between the heading and the next one, keep
 * the first cell, and drop repeats. Nothing subtler is needed, and nothing subtler should be:
 * if the table's shape changes, this must fail loudly rather than guess.
 *
 * @returns {string[]} Jurisdiction names, `EU` among them.
 * @throws {Error} If the annex is missing, or its table cannot be read.
 */
function readAnnexJurisdictions() {
  const document = readFileSync(ANNEX_PATH, 'utf8')
  const start = document.indexOf(ANNEX_HEADING)
  if (start === -1) throw new Error(`${ANNEX_PATH} has no "${ANNEX_HEADING}" heading.`)

  const names = []
  for (const line of document.slice(start + ANNEX_HEADING.length).split('\n')) {
    // The annex is followed by Annex 2a, which is a table of endpoints, not of jurisdictions.
    if (line.startsWith('#')) break
    if (!line.startsWith('|')) continue

    const first = line.split('|')[1]?.trim() ?? ''
    // The header row and the alignment row are the only two that are not jurisdictions.
    if (first === '' || first === 'Jurisdiction' || /^-+$/.test(first)) continue
    if (!names.includes(first)) names.push(first)
  }

  if (names.length < 2) throw new Error(`No jurisdiction rows found under "${ANNEX_HEADING}".`)
  if (!names.includes(EU_JURISDICTION)) {
    throw new Error(`Annex 2 no longer lists ${EU_JURISDICTION}, which the map draws as its own mark.`)
  }
  return names
}

/**
 * The jurisdictions to draw as shapes, resolved against Annex 2.
 *
 * @returns {{code: string, name: string, source: string}[]} One entry per member state.
 * @throws {Error} If the annex names a jurisdiction this script cannot place on the map, or if
 *   it has stopped naming one the script still knows how to draw.
 */
function jurisdictionsToDraw() {
  const annex = readAnnexJurisdictions().filter((name) => name !== EU_JURISDICTION)

  const unplaceable = annex.filter((name) => GEOMETRY_BY_JURISDICTION[name] === undefined)
  if (unplaceable.length > 0) {
    throw new Error(
      `Annex 2 lists ${unplaceable.join(', ')}, which the map has no geometry for. ` +
        'Add an ISO alpha-2 code and a Natural Earth numeric id to GEOMETRY_BY_JURISDICTION.',
    )
  }

  const dropped = Object.keys(GEOMETRY_BY_JURISDICTION).filter((name) => !annex.includes(name))
  if (dropped.length > 0) {
    throw new Error(
      `GEOMETRY_BY_JURISDICTION still knows ${dropped.join(', ')}, which Annex 2 no longer lists. ` +
        'Remove them, or restore the annex rows.',
    )
  }

  return annex.map((name) => ({ name, ...GEOMETRY_BY_JURISDICTION[name] }))
}

// ---------------------------------------------------------------------------------------
// TopoJSON
// ---------------------------------------------------------------------------------------

/**
 * Decode one TopoJSON arc into absolute lon/lat pairs.
 *
 * TopoJSON stores arcs delta-encoded on a quantised integer grid; this undoes both.
 *
 * @param {readonly (readonly number[])[]} arc - The encoded arc.
 * @param {{scale: readonly number[], translate: readonly number[]}} transform - Quantisation.
 * @returns {[number, number][]} Absolute positions.
 */
function decodeArc(arc, transform) {
  const [scaleX, scaleY] = transform.scale
  const [translateX, translateY] = transform.translate
  let x = 0
  let y = 0
  return arc.map(([dx, dy]) => {
    x += dx
    y += dy
    return [x * scaleX + translateX, y * scaleY + translateY]
  })
}

/**
 * Stitch a geometry object's arc indices into closed rings.
 *
 * @param {readonly number[]} ringArcs - Arc indices; a negative index means "reversed".
 * @param {readonly [number, number][][]} arcs - Decoded arcs.
 * @returns {[number, number][]} The ring.
 */
function stitchRing(ringArcs, arcs) {
  const ring = []
  for (const index of ringArcs) {
    const arc = index < 0 ? [...arcs[~index]].reverse() : arcs[index]
    // Consecutive arcs share their join point; keep it once.
    ring.push(...(ring.length === 0 ? arc : arc.slice(1)))
  }
  return ring
}

/**
 * Extract every ring of a TopoJSON country geometry.
 *
 * Only exterior rings are kept: holes (enclaves such as San Marino inside Italy) would need
 * a fill rule the shading does not want, and at this scale they are a pixel or two.
 *
 * @param {{type: string, arcs: unknown}} geometry - TopoJSON geometry object.
 * @param {readonly [number, number][][]} arcs - Decoded arcs.
 * @returns {[number, number][][]} Exterior rings in lon/lat.
 */
function geometryRings(geometry, arcs) {
  if (geometry.type === 'Polygon') return [stitchRing(geometry.arcs[0], arcs)]
  if (geometry.type === 'MultiPolygon') return geometry.arcs.map((polygon) => stitchRing(polygon[0], arcs))
  return []
}

// ---------------------------------------------------------------------------------------
// Clipping and projection
// ---------------------------------------------------------------------------------------

/**
 * Clip a ring to a rectangle (Sutherland–Hodgman, one edge at a time).
 *
 * Used twice: once in lon/lat to cull, once in canvas units to make the frame's edges.
 *
 * @param {readonly [number, number][]} ring - The ring.
 * @param {{west: number, east: number, south: number, north: number}} box - The rectangle,
 *   with `south` the lower and `north` the upper bound of the second coordinate.
 * @returns {[number, number][]} The clipped ring, empty when nothing is inside.
 */
function clipRing(ring, box) {
  const edges = [
    { inside: (p) => p[0] >= box.west, cut: (a, b) => interpolateX(a, b, box.west) },
    { inside: (p) => p[0] <= box.east, cut: (a, b) => interpolateX(a, b, box.east) },
    { inside: (p) => p[1] >= box.south, cut: (a, b) => interpolateY(a, b, box.south) },
    { inside: (p) => p[1] <= box.north, cut: (a, b) => interpolateY(a, b, box.north) },
  ]

  let current = ring
  for (const edge of edges) {
    /** @type {[number, number][]} */
    const next = []
    for (let index = 0; index < current.length; index += 1) {
      const point = current[index]
      const previous = current[(index + current.length - 1) % current.length]
      const pointIn = edge.inside(point)
      const previousIn = edge.inside(previous)
      if (pointIn !== previousIn) next.push(edge.cut(previous, point))
      if (pointIn) next.push(point)
    }
    current = next
    if (current.length === 0) return []
  }
  return current
}

/**
 * Point where a segment crosses a meridian.
 *
 * @param {readonly number[]} a - Segment start.
 * @param {readonly number[]} b - Segment end.
 * @param {number} longitude - The meridian.
 * @returns {[number, number]} The crossing point.
 */
function interpolateX(a, b, longitude) {
  const t = (longitude - a[0]) / (b[0] - a[0])
  return [longitude, a[1] + t * (b[1] - a[1])]
}

/**
 * Point where a segment crosses a parallel.
 *
 * @param {readonly number[]} a - Segment start.
 * @param {readonly number[]} b - Segment end.
 * @param {number} latitude - The parallel.
 * @returns {[number, number]} The crossing point.
 */
function interpolateY(a, b, latitude) {
  const t = (latitude - a[1]) / (b[1] - a[1])
  return [a[0] + t * (b[0] - a[0]), latitude]
}

const DEGREES = Math.PI / 180

/**
 * Build the Lambert conformal conic projection onto an abstract unit plane.
 *
 * @returns {(position: readonly number[]) => [number, number]} lon/lat to plane coordinates.
 */
function buildProjection() {
  const phi1 = PROJECTION.parallel1 * DEGREES
  const phi2 = PROJECTION.parallel2 * DEGREES
  const phi0 = PROJECTION.referenceLatitude * DEGREES
  const lambda0 = PROJECTION.referenceLongitude * DEGREES

  /**
   * The conic's radial term.
   *
   * @param {number} phi - Latitude in radians.
   * @returns {number} tan(pi/4 + phi/2).
   */
  const t = (phi) => Math.tan(Math.PI / 4 + phi / 2)

  const n = Math.log(Math.cos(phi1) / Math.cos(phi2)) / Math.log(t(phi2) / t(phi1))
  const f = (Math.cos(phi1) * t(phi1) ** n) / n
  const rho0 = f / t(phi0) ** n

  return ([longitude, latitude]) => {
    const rho = f / t(latitude * DEGREES) ** n
    const theta = n * (longitude * DEGREES - lambda0)
    // The conic's y grows northwards; a screen's grows downwards, so it is negated here
    // rather than in every consumer.
    return [rho * Math.sin(theta), rho * Math.cos(theta) - rho0]
  }
}

/**
 * Fit the projection's plane to the canvas.
 *
 * The window's own boundary is sampled densely, so the canvas is exactly the projected
 * window: no guesswork, and nothing that should be visible falls outside the `viewBox`.
 *
 * @param {(position: readonly number[]) => [number, number]} project - Plane projection.
 * @returns {{width: number, height: number, toCanvas: (position: readonly number[]) => [number, number]}}
 *   Canvas size and the lon/lat to user-unit transform.
 */
function fitToCanvas(project) {
  const samples = []
  const steps = 200
  for (let index = 0; index <= steps; index += 1) {
    const fraction = index / steps
    const longitude = VIEW.west + fraction * (VIEW.east - VIEW.west)
    const latitude = VIEW.south + fraction * (VIEW.north - VIEW.south)
    samples.push([longitude, VIEW.south], [longitude, VIEW.north])
    samples.push([VIEW.west, latitude], [VIEW.east, latitude])
  }

  const projected = samples.map(project)
  const minX = Math.min(...projected.map((point) => point[0]))
  const maxX = Math.max(...projected.map((point) => point[0]))
  const minY = Math.min(...projected.map((point) => point[1]))
  const maxY = Math.max(...projected.map((point) => point[1]))

  const scale = CANVAS_WIDTH / (maxX - minX)
  const height = Math.round((maxY - minY) * scale)

  return {
    width: CANVAS_WIDTH,
    height,
    toCanvas: (position) => {
      const [x, y] = project(position)
      return [(x - minX) * scale, (y - minY) * scale]
    },
  }
}

// ---------------------------------------------------------------------------------------
// Simplification and path emission
// ---------------------------------------------------------------------------------------

/**
 * Perpendicular distance from a point to a segment.
 *
 * @param {readonly number[]} point - The point.
 * @param {readonly number[]} start - Segment start.
 * @param {readonly number[]} end - Segment end.
 * @returns {number} The distance.
 */
function segmentDistance(point, start, end) {
  const dx = end[0] - start[0]
  const dy = end[1] - start[1]
  const lengthSquared = dx * dx + dy * dy
  if (lengthSquared === 0) return Math.hypot(point[0] - start[0], point[1] - start[1])
  const t = Math.max(0, Math.min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / lengthSquared))
  return Math.hypot(point[0] - (start[0] + t * dx), point[1] - (start[1] + t * dy))
}

/**
 * Douglas–Peucker simplification, iterative so a long coastline cannot blow the stack.
 *
 * @param {readonly [number, number][]} points - Polyline in user units.
 * @param {number} tolerance - Maximum deviation allowed.
 * @returns {[number, number][]} The simplified polyline.
 */
function simplify(points, tolerance) {
  if (points.length < 3) return [...points]

  const keep = new Array(points.length).fill(false)
  keep[0] = true
  keep[points.length - 1] = true

  const stack = [[0, points.length - 1]]
  while (stack.length > 0) {
    const [first, last] = stack.pop()
    let farthest = -1
    let distance = tolerance
    for (let index = first + 1; index < last; index += 1) {
      const candidate = segmentDistance(points[index], points[first], points[last])
      if (candidate > distance) {
        distance = candidate
        farthest = index
      }
    }
    if (farthest !== -1) {
      keep[farthest] = true
      stack.push([first, farthest], [farthest, last])
    }
  }

  return points.filter((_point, index) => keep[index])
}

/**
 * Signed area of a ring, used both to drop specks and to find a label point.
 *
 * @param {readonly [number, number][]} ring - The ring in user units.
 * @returns {number} Twice the signed area.
 */
function doubleArea(ring) {
  let total = 0
  for (let index = 0; index < ring.length; index += 1) {
    const [x1, y1] = ring[index]
    const [x2, y2] = ring[(index + 1) % ring.length]
    total += x1 * y2 - x2 * y1
  }
  return total
}

/**
 * Centroid of a ring, falling back to the mean vertex for a degenerate one.
 *
 * @param {readonly [number, number][]} ring - The ring in user units.
 * @returns {[number, number]} The centroid.
 */
function centroid(ring) {
  const area = doubleArea(ring)
  if (Math.abs(area) < 1e-9) {
    const sum = ring.reduce((acc, point) => [acc[0] + point[0], acc[1] + point[1]], [0, 0])
    return [sum[0] / ring.length, sum[1] / ring.length]
  }
  let x = 0
  let y = 0
  for (let index = 0; index < ring.length; index += 1) {
    const [x1, y1] = ring[index]
    const [x2, y2] = ring[(index + 1) % ring.length]
    const cross = x1 * y2 - x2 * y1
    x += (x1 + x2) * cross
    y += (y1 + y2) * cross
  }
  return [x / (3 * area), y / (3 * area)]
}

/**
 * Round a coordinate to one decimal place, which is a tenth of a user unit.
 *
 * @param {number} value - The coordinate.
 * @returns {string} Its shortest decimal form.
 */
function round(value) {
  return String(Math.round(value * 10) / 10)
}

/**
 * Emit rings as one SVG path.
 *
 * @param {readonly [number, number][][]} rings - Rings in user units.
 * @returns {string} The `d` attribute.
 */
function toPath(rings) {
  return rings
    .map((ring) => `M${ring.map(([x, y]) => `${round(x)} ${round(y)}`).join('L')}Z`)
    .join('')
}

// ---------------------------------------------------------------------------------------
// Assembly
// ---------------------------------------------------------------------------------------

/**
 * Read the source TopoJSON, downloading it on first use.
 *
 * @param {string} cacheDirectory - Where the download is kept.
 * @returns {Promise<object>} The parsed topology.
 */
async function loadTopology(cacheDirectory) {
  const cached = join(cacheDirectory, SOURCE_FILE)
  try {
    return JSON.parse(readFileSync(cached, 'utf8'))
  } catch {
    process.stderr.write(`Downloading ${SOURCE_URL}\n`)
    const response = await fetch(SOURCE_URL)
    if (!response.ok) throw new Error(`Source data request failed with status ${response.status}`)
    const body = await response.text()
    mkdirSync(cacheDirectory, { recursive: true })
    writeFileSync(cached, body)
    return JSON.parse(body)
  }
}

/**
 * Project, clip and simplify one country's rings.
 *
 * @param {readonly [number, number][][]} rings - Rings in lon/lat.
 * @param {{width: number, height: number, toCanvas: (position: readonly number[]) => [number, number]}} canvas -
 *   The fitted canvas.
 * @returns {[number, number][][]} Renderable rings in user units, largest first.
 */
function prepareRings(rings, canvas) {
  const frame = { west: 0, east: canvas.width, south: 0, north: canvas.height }
  return rings
    .map((ring) => clipRing(ring, CULL))
    .filter((ring) => ring.length >= 3)
    .map((ring) => clipRing(ring.map(canvas.toCanvas), frame))
    .filter((ring) => ring.length >= 3)
    .map((ring) => simplify(ring, SIMPLIFY_TOLERANCE))
    .filter((ring) => ring.length >= 3 && Math.abs(doubleArea(ring)) / 2 >= MIN_RING_AREA)
    .sort((a, b) => Math.abs(doubleArea(b)) - Math.abs(doubleArea(a)))
}

/**
 * Where a country's outline lies on the globe, in degrees.
 *
 * Deliberately computed **before** the projection, from the same rings the path is drawn from:
 * a lon/lat position is a statement about the Earth, so it can be checked against ordinary
 * geographic knowledge — Austria is around 47°N 14°E — by a test that knows nothing about the
 * projection, the canvas or the frame. A label point in user units cannot do that: it moves
 * whenever the projection is tuned, and says nothing about which country was drawn.
 *
 * The largest ring is the one taken, so an overseas territory or an island cannot pull the
 * anchor away from the country the reader sees; the same {@link CULL} box the drawing uses is
 * applied first, so what is measured is what was rendered.
 *
 * @param {readonly [number, number][][]} rings - The country's rings in lon/lat.
 * @returns {{lon: number, lat: number}} The centroid of its largest rendered ring.
 * @throws {Error} If nothing of the country survives the cull, which would leave the map with
 *   a shape it cannot place.
 */
function geographicAnchor(rings) {
  const culled = rings.map((ring) => clipRing(ring, CULL)).filter((ring) => ring.length >= 3)
  if (culled.length === 0) throw new Error('A jurisdiction has no geometry inside the culling box.')

  const largest = culled.reduce((best, ring) =>
    Math.abs(doubleArea(ring)) > Math.abs(doubleArea(best)) ? ring : best,
  )
  const [lon, lat] = centroid(largest)
  return { lon: Math.round(lon * 10) / 10, lat: Math.round(lat * 10) / 10 }
}

/**
 * Build the whole geometry payload.
 *
 * @param {object} topology - Parsed TopoJSON.
 * @returns {object} Everything the generated module exports, plus statistics for the log.
 */
function buildGeometry(topology) {
  const arcs = topology.arcs.map((arc) => decodeArc(arc, topology.transform))
  const project = buildProjection()
  const canvas = fitToCanvas(project)

  const wanted = new Map(jurisdictionsToDraw().map((entry) => [entry.source, entry]))
  const shapes = []
  const contextRings = []
  const coastline = []

  for (const geometry of topology.objects.countries.geometries) {
    const sourceRings = geometryRings(geometry, arcs)
    const rings = prepareRings(sourceRings, canvas)
    if (rings.length === 0) continue
    for (const ring of rings) coastline.push(...ring)

    const jurisdiction = wanted.get(String(geometry.id))
    if (jurisdiction === undefined) {
      contextRings.push(...rings)
      continue
    }

    const largestArea = Math.abs(doubleArea(rings[0])) / 2
    const labelPoint = centroid(rings[0])
    shapes.push({
      code: jurisdiction.code,
      name: jurisdiction.name,
      path: toPath(rings),
      labelPoint: [Number(round(labelPoint[0])), Number(round(labelPoint[1]))],
      anchor: geographicAnchor(sourceRings),
      needsMarker: largestArea < MARKER_AREA_THRESHOLD,
      area: Math.round(largestArea),
    })
    wanted.delete(String(geometry.id))
  }

  if (wanted.size > 0) {
    const missing = [...wanted.values()].map((entry) => entry.name).join(', ')
    throw new Error(`Annex 2 jurisdictions missing from the source data: ${missing}`)
  }

  const marker = canvas.toCanvas([EU_MARKER.longitude, EU_MARKER.latitude])
  const clearance = coastline.reduce(
    (nearest, point) => Math.min(nearest, Math.hypot(point[0] - marker[0], point[1] - marker[1])),
    Number.POSITIVE_INFINITY,
  )
  if (clearance < EU_MARKER.radius + MIN_EU_CLEARANCE) {
    throw new Error(
      `The EU marker is ${clearance.toFixed(1)} units from land but needs ` +
        `${EU_MARKER.radius + MIN_EU_CLEARANCE}. Move it in EU_MARKER.`,
    )
  }

  shapes.sort((a, b) => a.code.localeCompare(b.code))

  return {
    width: canvas.width,
    height: canvas.height,
    shapes,
    contextPath: toPath(contextRings),
    marker: {
      x: Number(round(marker[0])),
      y: Number(round(marker[1])),
      radius: EU_MARKER.radius,
      clearance: Math.round(clearance * 10) / 10,
    },
  }
}

/**
 * Render the generated TypeScript module.
 *
 * @param {object} geometry - Output of {@link buildGeometry}.
 * @returns {string} The module source.
 */
function renderModule(geometry) {
  const shapes = geometry.shapes
    .map(
      (shape) =>
        `  {\n` +
        `    code: '${shape.code}',\n` +
        `    name: '${shape.name}',\n` +
        `    labelPoint: { x: ${shape.labelPoint[0]}, y: ${shape.labelPoint[1]} },\n` +
        `    needsMarker: ${String(shape.needsMarker)},\n` +
        `    path:\n      '${shape.path}',\n` +
        `  },`,
    )
    .join('\n')

  const anchors = geometry.shapes
    .map((shape) => `  ${shape.code}: { lon: ${shape.anchor.lon}, lat: ${shape.anchor.lat} },`)
    .join('\n')

  return `/**
 * Pre-projected geometry for the jurisdiction map. **Generated — do not edit by hand.**
 *
 * Regenerate with \`node scripts/generate-map-geometry.mjs\` from \`frontend/\`; that script
 * documents the source data, the projection and why the map ships as plain path strings
 * rather than as a mapping library. \`--check\` fails when this file is out of date.
 *
 * Coverage: the ${geometry.shapes.length} member states of \`docs/core-document.md\` Annex 2,
 * read from that table rather than restated here, plus the European Union, which the map draws
 * as a mark in the North Sea instead of a shape.
 * Source: Natural Earth 1:50m admin-0 countries (public domain) via world-atlas@2.0.2.
 * Projection: Lambert conformal conic, standard parallels ${PROJECTION.parallel1}°N and
 * ${PROJECTION.parallel2}°N, framing ${Math.abs(VIEW.west)}°W–${VIEW.east}°E,
 * ${VIEW.south}°N–${VIEW.north}°N.
 */

/** One jurisdiction's renderable geometry. */
export interface JurisdictionShape {
  /** \`map_feature_id\`: the ISO 3166-1 alpha-2 code (\`docs/architecture.md\` section 3). */
  readonly code: string
  /** English name of the jurisdiction, as Annex 2 lists it. */
  readonly name: string
  /** Where the tooltip is anchored when the shape is reached by keyboard. */
  readonly labelPoint: { readonly x: number; readonly y: number }
  /** Whether the shape is too small to hit, and needs a marker of its own as well. */
  readonly needsMarker: boolean
  /** The \`d\` attribute of the shape's path. */
  readonly path: string
}

/** The \`viewBox\` the paths are drawn in. */
export const MAP_VIEW_BOX = { width: ${geometry.width}, height: ${geometry.height} } as const

/** Radius of the pointer target given to a jurisdiction whose shape is too small to hit. */
export const MARKER_RADIUS = ${MARKER_RADIUS}

/**
 * The EU marker, in the North Sea. The generator refuses to write this file unless the
 * marker clears every rendered coastline; the clearance measured for these values was
 * ${geometry.marker.clearance} user units against a radius of ${geometry.marker.radius}.
 */
export const EU_MARKER = { x: ${geometry.marker.x}, y: ${geometry.marker.y}, radius: ${geometry.marker.radius} } as const

/** Every country outside Annex 2, as one path: context for the reader, never interactive. */
export const CONTEXT_PATH =
  '${geometry.contextPath}'

/** The Annex 2 jurisdictions, in code order. */
export const JURISDICTION_SHAPES: readonly JurisdictionShape[] = [
${shapes}
]

/** Where a jurisdiction's outline lies on the globe, in degrees east and north. */
export interface GeographicAnchor {
  /** Longitude, positive east of Greenwich. */
  readonly lon: number
  /** Latitude, positive north of the equator. */
  readonly lat: number
}

/**
 * Where each outline above was taken from, in unprojected lon/lat, keyed by ISO alpha-2 code.
 *
 * **Nothing renders this**, and nothing should: the map is drawn from \`path\`, and these
 * degrees never reach the browser. They are the geometry's own account of *which country*
 * each shape is — measured on the globe, before the projection touched it — so that
 * \`tests/mapGeometry.test.ts\` can hold every shape against ordinary geographic knowledge of
 * where that country is. An outline drawn from the wrong Natural Earth id is a plausible map
 * of the wrong country and nothing in the path data can tell; its anchor lands hundreds of
 * kilometres from where the named country belongs, and fails.
 *
 * Each is the centroid of the largest ring the generator kept for that jurisdiction, to a
 * tenth of a degree — around eleven kilometres, which is far finer than the question being
 * asked of it.
 */
export const JURISDICTION_ANCHORS: Readonly<Record<string, GeographicAnchor>> = {
${anchors}
}
`
}

/**
 * Entry point.
 *
 * @returns {Promise<void>} Resolves once the module is written or checked.
 */
async function main() {
  const check = process.argv.includes('--check')
  const cacheIndex = process.argv.indexOf('--cache')
  const cacheDirectory =
    cacheIndex === -1 ? join(FRONTEND_ROOT, 'node_modules', '.cache', 'plt-map') : process.argv[cacheIndex + 1]

  const topology = await loadTopology(cacheDirectory)
  const geometry = buildGeometry(topology)
  const source = renderModule(geometry)

  if (check) {
    const existing = readFileSync(OUTPUT_PATH, 'utf8')
    if (existing !== source) throw new Error('geometry.generated.ts is out of date; re-run this script.')
    process.stderr.write('geometry.generated.ts is up to date.\n')
    return
  }

  mkdirSync(dirname(OUTPUT_PATH), { recursive: true })
  writeFileSync(OUTPUT_PATH, source)

  const markers = geometry.shapes.filter((shape) => shape.needsMarker).map((shape) => shape.code)
  process.stderr.write(
    [
      `viewBox           ${geometry.width} x ${geometry.height}`,
      `jurisdictions     ${geometry.shapes.length}`,
      `pointer markers   ${markers.join(', ') || 'none'}`,
      `EU marker         (${geometry.marker.x}, ${geometry.marker.y}) clearance ${geometry.marker.clearance}`,
      `path data         ${Math.round(source.length / 1024)} kB`,
      `sha256            ${createHash('sha256').update(source).digest('hex').slice(0, 16)}`,
      '',
    ].join('\n'),
  )
}

// Only when run as a command. `tests/mapGeometry.test.ts` imports GEOMETRY_BY_JURISDICTION from
// this module to check it against ISO 3166-1, and must not set the generator going to do so.
if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main()
}
