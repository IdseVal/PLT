/**
 * The map of jurisdictions: the home page's signature element.
 *
 * A map of Europe with one hoverable shape per jurisdiction of `docs/CORE_DOCUMENT.md`
 * Annex 2, shaded by how many cases the tracker holds for it, plus the European Union as a
 * mark in the North Sea — a jurisdiction in its own right, never the sum of its member
 * states (core document section 3.3). Selecting a jurisdiction opens its cases.
 *
 * ## Why inline SVG
 *
 * The geometry is projected once, at development time, by `scripts/generate-map-geometry.mjs`
 * and imported as plain path strings. The browser therefore loads no mapping library, runs no
 * projection and contacts no tile server, which is what issue #13 and `docs/architecture.md`
 * section 6 require: the tracker has to work offline and load fast.
 *
 * ## Data
 *
 * One request on mount, for the whole map (`docs/architecture.md` section 5.1). Hovering,
 * focusing and moving between shapes all resolve against data already in memory, so no
 * interaction causes a request. Coverage comes from the geometry, not from the response: a
 * jurisdiction the API does not mention is drawn in its "no cases yet" state rather than
 * disappearing, which at launch is every jurisdiction but the Netherlands and the Union.
 *
 * ## Reaching it without a pointer
 *
 * Every jurisdiction is a link in the document, focusable with `Tab` and activated with
 * `Enter` or `Space`. Its accessible name carries the jurisdiction and its count in words,
 * so a screen reader announces "Netherlands: 42 cases, link" without the tooltip, which is
 * decorative and hidden from assistive technology. Focus draws the same outline as hover and
 * anchors the same tooltip at the shape, so what a pointer shows and what a keyboard shows
 * are the same thing.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FocusEvent, KeyboardEvent, MouseEvent, PointerEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import type { ApiError } from '@/api/client'
import { euStarsPath } from '@/components/map/euEmblem'
import {
  CONTEXT_PATH,
  EU_MARKER,
  JURISDICTION_SHAPES,
  MAP_VIEW_BOX,
  MARKER_RADIUS,
} from '@/components/map/geometry.generated'
import type { CountBand } from '@/components/map/shading'
import { BAND_FILL, BAND_LEGEND, BAND_SWATCH, bandOf, countPhrase } from '@/components/map/shading'
import { useJurisdictionStats } from '@/hooks/useJurisdictionStats'
import { cleanInlineText } from '@/utils/caseText'
import { jurisdictionCasesPath } from '@/utils/links'

/** `map_feature_id` of the Union (`docs/architecture.md` section 3). */
const EU_CODE = 'EU'

/** Name shown for the Union until the API supplies its own. */
const EU_NAME = 'European Union'

/** Attribute the pointer and focus handlers read a jurisdiction out of the DOM with. */
const FEATURE_ATTRIBUTE = 'data-jurisdiction'

/**
 * Hairline between neighbouring countries, in **CSS pixels**.
 *
 * Scaled into user units from the map's rendered width like everything else that has to keep
 * its size on screen. Fixed in user units it would thin to a quarter of a pixel on a phone,
 * and a dozen neighbouring jurisdictions with no cases yet would read as one shape.
 */
const BORDER_WIDTH = 0.6

/** Width of the outline drawn round the jurisdiction under the pointer, in CSS pixels. */
const HIGHLIGHT_WIDTH = 2

/** Gap between the pointer and the tooltip, in CSS pixels. */
const TOOLTIP_OFFSET = 14

/** Height of the tooltip, in CSS pixels. Two lines of `text-xs` and its padding. */
const TOOLTIP_HEIGHT = 42

/** Horizontal padding inside the tooltip, in CSS pixels. */
const TOOLTIP_PADDING = 10

/**
 * Rough width of one character of the tooltip's type, in CSS pixels.
 *
 * The tooltip is drawn in SVG, where a box cannot size itself to its text, so the box is
 * estimated from the character count. It is deliberately generous: a box slightly too wide
 * is invisible, whereas one too narrow clips the count.
 */
const TOOLTIP_CHARACTER_WIDTH = 7

/**
 * Radius of the emblem's dark field, as a fraction of the marker's.
 *
 * The marker is two circles: an outer ring carrying the shading band, like any other
 * jurisdiction, and inside it the emblem's field, which is always dark so the stars keep
 * their contrast whether the Union holds no cases or thousands.
 */
const EU_FIELD_RATIO = 0.72

/** Radius of that field, in user units. */
const EU_FIELD_RADIUS = EU_MARKER.radius * EU_FIELD_RATIO

/** The emblem's stars, computed once: the disc never moves. */
const EU_STARS = euStarsPath(EU_MARKER.x, EU_MARKER.y, EU_FIELD_RADIUS)

/** One jurisdiction as the map draws it. */
interface MapFeature {
  /** `map_feature_id`: the alpha-2 code, or `EU`. */
  readonly code: string
  /** Name for the tooltip and the accessible name. */
  readonly name: string
  /** Case count, or `null` while unknown. */
  readonly count: number | null
  /** Shading band the count falls in. */
  readonly band: CountBand
  /** The country outline, or `null` for the Union, which is a mark rather than a shape. */
  readonly path: string | null
  /** Where the tooltip is anchored when the shape is reached by keyboard. */
  readonly labelPoint: { readonly x: number; readonly y: number }
  /** Whether the shape is too small to hit and needs a marker of its own. */
  readonly needsMarker: boolean
  /** The count in words, e.g. `42 cases`. */
  readonly phrase: string
}

/**
 * The jurisdiction an event happened in, if any.
 *
 * Read off the DOM rather than tracked per shape on purpose: one handler on the `<svg>`
 * decides which jurisdiction the pointer is in, so crossing a border is a single update from
 * one jurisdiction to the next. Per-shape enter and leave handlers would fire as two events,
 * and the tooltip would blink at every boundary.
 *
 * @param target - Event target.
 * @returns The jurisdiction's code, or `null` when the target is sea, context or the frame.
 */
function featureCodeAt(target: unknown): string | null {
  if (!(target instanceof Element)) return null
  return target.closest(`[${FEATURE_ATTRIBUTE}]`)?.getAttribute(FEATURE_ATTRIBUTE) ?? null
}

/**
 * Message for a failed request.
 *
 * @param error - The failure.
 * @returns A sentence to show the reader.
 */
function errorMessage(error: ApiError): string {
  switch (error.code) {
    case 'network_error':
      return 'The tracker could not reach the case database. It may be offline for maintenance.'
    case 'request_aborted':
      return 'Loading the case counts took too long.'
    case 'invalid_response':
      return 'The case database answered in a form this map could not read.'
    default:
      return error.message
  }
}

/** Properties of {@link JurisdictionMap}. */
export interface JurisdictionMapProps {
  /** Extra classes for the slot, so the page owns the size and the component owns the look. */
  readonly className?: string
}

/**
 * Render the jurisdiction map.
 *
 * @param props - Component properties.
 * @returns The map section.
 */
export default function JurisdictionMap({ className = '' }: JurisdictionMapProps): JSX.Element {
  const navigate = useNavigate()
  const { state, byFeatureId, reload } = useJurisdictionStats()

  const svgRef = useRef<SVGSVGElement | null>(null)
  const [hovered, setHovered] = useState<string | null>(null)
  const [focused, setFocused] = useState<string | null>(null)
  const [pointer, setPointer] = useState<{ readonly x: number; readonly y: number } | null>(null)

  /**
   * User units per CSS pixel.
   *
   * The map scales with its column, so anything that has to keep a constant size on screen —
   * the tooltip's type, the outline, the pointer target given to a country too small to hit —
   * is measured in CSS pixels and multiplied by this. Without it the tooltip would be
   * unreadable on a phone, where the map is a third of its desktop width.
   */
  const [unit, setUnit] = useState(1)

  useEffect((): (() => void) | undefined => {
    const element = svgRef.current
    if (element === null || typeof ResizeObserver === 'undefined') return undefined

    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0
      if (width > 0) setUnit(MAP_VIEW_BOX.width / width)
    })
    observer.observe(element)
    return (): void => {
      observer.disconnect()
    }
  }, [])

  const features = useMemo((): readonly MapFeature[] => {
    const known = state.status === 'ready'

    /**
     * Assemble one feature from its geometry and whatever the API said about it.
     *
     * @param code - The jurisdiction's `map_feature_id`.
     * @param fallbackName - Name to use when the API did not supply one.
     * @param path - The country outline, or `null` for the Union.
     * @param labelPoint - Anchor for the tooltip.
     * @param needsMarker - Whether the shape needs a pointer target of its own.
     * @returns The feature.
     */
    const build = (
      code: string,
      fallbackName: string,
      path: string | null,
      labelPoint: { readonly x: number; readonly y: number },
      needsMarker: boolean,
    ): MapFeature => {
      const stat = byFeatureId.get(code)
      // A jurisdiction the payload does not mention has no cases yet; it is not missing.
      const count = known ? (stat?.case_count ?? 0) : null
      return {
        code,
        // The API's name is server-supplied text and goes through `cleanInlineText` like
        // every other such string (`src/utils/caseText.ts`), which is what keeps a control
        // character or a bidirectional override out of the tooltip and the accessible name.
        // `fallbackName` is repo-authored, from the generated geometry, and needs no
        // cleaning; a name that cleans away to nothing falls back to it.
        name: cleanInlineText(stat?.name) || fallbackName,
        count,
        band: bandOf(count),
        path,
        labelPoint,
        needsMarker,
        phrase: count === null && state.status === 'loading' ? 'case count loading' : countPhrase(count),
      }
    }

    return [
      ...JURISDICTION_SHAPES.map((shape) =>
        build(shape.code, shape.name, shape.path, shape.labelPoint, shape.needsMarker),
      ),
      build(EU_CODE, EU_NAME, null, { x: EU_MARKER.x, y: EU_MARKER.y }, false),
    ]
  }, [byFeatureId, state.status])

  const activeCode = hovered ?? focused
  const active = features.find((feature) => feature.code === activeCode) ?? null

  const handlePointerMove = useCallback((event: PointerEvent<SVGSVGElement>): void => {
    const code = featureCodeAt(event.target)
    setHovered(code)
    setPointer(code === null ? null : toViewBoxPoint(svgRef.current, event.clientX, event.clientY))
  }, [])

  const handlePointerLeave = useCallback((): void => {
    setHovered(null)
    setPointer(null)
  }, [])

  const handleFocus = useCallback((event: FocusEvent<SVGSVGElement>): void => {
    setFocused(featureCodeAt(event.target))
  }, [])

  const handleBlur = useCallback((): void => {
    setFocused(null)
  }, [])

  const activate = useCallback(
    (code: string): void => {
      navigate(jurisdictionCasesPath(code))
    },
    [navigate],
  )

  const handleClick = useCallback(
    (event: MouseEvent, code: string): void => {
      // A modified click is the reader asking their browser for a new tab or window; the
      // `href` is real, so letting it through is the right answer.
      if (event.defaultPrevented || event.button !== 0) return
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      event.preventDefault()
      activate(code)
    },
    [activate],
  )

  const handleKeyDown = useCallback(
    (event: KeyboardEvent, code: string): void => {
      // Enter is a link's own activation and Space is a button's; a map shape reads as both,
      // and a reader who has just tabbed onto a country should not have to guess which.
      if (event.key !== 'Enter' && event.key !== ' ') return
      event.preventDefault()
      activate(code)
    },
    [activate],
  )

  const withCases = features.filter((feature) => (feature.count ?? 0) > 0).length
  const statusMessage =
    state.status === 'loading'
      ? 'Loading the case count for each jurisdiction.'
      : state.status === 'ready'
        ? `Case counts loaded. ${String(withCases)} of ${String(features.length)} jurisdictions hold cases.`
        : ''

  const markerRadius = MARKER_RADIUS * unit
  const highlightWidth = HIGHLIGHT_WIDTH * unit
  const borderWidth = BORDER_WIDTH * unit
  const anchor = active === null ? null : (hovered !== null ? pointer : null) ?? active.labelPoint

  return (
    <section
      aria-labelledby="map-slot-heading"
      className={`border-plt-border bg-plt-panel flex flex-col gap-4 rounded border p-4 sm:p-5 ${className}`}
    >
      <div className="space-y-1">
        <h2 id="map-slot-heading" className="text-plt-accent-deep font-display text-xl font-bold">
          Map of jurisdictions
        </h2>
        <p className="text-plt-muted text-sm leading-relaxed">
          Every jurisdiction the tracker covers, shaded by how many cases it holds. Point at one, or
          move through them with the Tab key, to read its count; select it to open its cases.
        </p>
      </div>

      {state.status === 'error' && (
        <div role="alert" className="border-plt-border bg-plt-accent-soft space-y-3 rounded border p-4">
          <p className="text-plt-ink text-sm font-semibold">The case counts could not be loaded.</p>
          <p className="text-plt-muted text-sm leading-relaxed">{errorMessage(state.error)}</p>
          <button
            type="button"
            onClick={reload}
            className="border-plt-accent-strong text-plt-accent-strong rounded border px-3 py-1.5 text-sm font-semibold"
          >
            Try again
          </button>
        </div>
      )}

      <p role="status" aria-live="polite" className="sr-only">
        {statusMessage}
      </p>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${String(MAP_VIEW_BOX.width)} ${String(MAP_VIEW_BOX.height)}`}
        preserveAspectRatio="xMidYMid meet"
        role="group"
        aria-label="Map of Europe"
        className="mx-auto block h-auto w-full max-w-[44rem]"
        onPointerMove={handlePointerMove}
        onPointerLeave={handlePointerLeave}
        onFocus={handleFocus}
        onBlur={handleBlur}
      >
        <rect
          x={0}
          y={0}
          width={MAP_VIEW_BOX.width}
          height={MAP_VIEW_BOX.height}
          rx={4}
          className="fill-plt-surface stroke-plt-border"
          strokeWidth={unit}
        />
        <path
          d={CONTEXT_PATH}
          aria-hidden="true"
          className="fill-plt-border stroke-plt-panel"
          strokeWidth={borderWidth}
        />

        {features.map((feature) => (
          <a
            key={feature.code}
            href={jurisdictionCasesPath(feature.code)}
            role="link"
            tabIndex={0}
            aria-label={`${feature.name}: ${feature.phrase}`}
            className="cursor-pointer"
            onClick={(event) => {
              handleClick(event, feature.code)
            }}
            onKeyDown={(event) => {
              handleKeyDown(event, feature.code)
            }}
            data-jurisdiction={feature.code}
          >
            {feature.path !== null && (
              <path
                d={feature.path}
                className={`${BAND_FILL[feature.band]} stroke-plt-panel`}
                strokeWidth={borderWidth}
              />
            )}
            {feature.needsMarker && (
              <circle
                cx={feature.labelPoint.x}
                cy={feature.labelPoint.y}
                r={markerRadius}
                className={`${BAND_FILL[feature.band]} stroke-plt-panel`}
                strokeWidth={highlightWidth / 2}
              />
            )}
            {feature.code === EU_CODE && (
              <>
                <circle
                  cx={EU_MARKER.x}
                  cy={EU_MARKER.y}
                  r={EU_MARKER.radius}
                  className={`${BAND_FILL[feature.band]} stroke-plt-accent-deep`}
                  strokeWidth={borderWidth * 2}
                />
                <circle
                  cx={EU_MARKER.x}
                  cy={EU_MARKER.y}
                  r={EU_FIELD_RADIUS}
                  aria-hidden="true"
                  className="fill-plt-accent-deep"
                />
                <path d={EU_STARS} aria-hidden="true" className="fill-plt-inverse" />
              </>
            )}
          </a>
        ))}

        {active !== null && (
          <g aria-hidden="true" className="pointer-events-none">
            {active.path !== null && (
              <path d={active.path} className="fill-none stroke-plt-ink" strokeWidth={highlightWidth} />
            )}
            {(active.needsMarker || active.code === EU_CODE) && (
              <circle
                cx={active.labelPoint.x}
                cy={active.labelPoint.y}
                r={active.code === EU_CODE ? EU_MARKER.radius : markerRadius}
                className="fill-none stroke-plt-ink"
                strokeWidth={highlightWidth}
              />
            )}
          </g>
        )}

        {active !== null && anchor !== null && (
          <MapTooltip name={active.name} phrase={active.phrase} anchor={anchor} unit={unit} />
        )}
      </svg>

      <ul className="text-plt-muted m-0 flex list-none flex-wrap gap-x-4 gap-y-2 p-0 text-xs">
        {BAND_LEGEND.map((entry) => (
          <li key={entry.band} className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className={`${BAND_SWATCH[entry.band]} border-plt-border inline-block h-3 w-3 rounded-sm border`}
            />
            {entry.label}
          </li>
        ))}
        <li className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="bg-plt-border border-plt-border inline-block h-3 w-3 rounded-sm border"
          />
          Outside the tracker&rsquo;s coverage
        </li>
      </ul>
    </section>
  )
}

/**
 * Convert a pointer position into `viewBox` coordinates.
 *
 * `getScreenCTM` would do this too, but it is not implemented everywhere the tests run, and
 * the arithmetic is the whole of it: the map is drawn with `xMidYMid meet`, so it is scaled
 * uniformly and centred in whatever box the layout gives it.
 *
 * @param svg - The map's root element, or `null` before it mounts.
 * @param clientX - Pointer position, x.
 * @param clientY - Pointer position, y.
 * @returns The position in user units, or `null` when the element has no size yet.
 */
function toViewBoxPoint(
  svg: SVGSVGElement | null,
  clientX: number,
  clientY: number,
): { readonly x: number; readonly y: number } | null {
  if (svg === null) return null

  const box = svg.getBoundingClientRect()
  if (box.width <= 0 || box.height <= 0) return null

  const scale = Math.min(box.width / MAP_VIEW_BOX.width, box.height / MAP_VIEW_BOX.height)
  if (scale <= 0) return null

  return {
    x: (clientX - box.left - (box.width - MAP_VIEW_BOX.width * scale) / 2) / scale,
    y: (clientY - box.top - (box.height - MAP_VIEW_BOX.height * scale) / 2) / scale,
  }
}

/** Properties of {@link MapTooltip}. */
interface MapTooltipProps {
  /** Jurisdiction name. */
  readonly name: string
  /** The count in words. */
  readonly phrase: string
  /** Where to point, in user units. */
  readonly anchor: { readonly x: number; readonly y: number }
  /** User units per CSS pixel. */
  readonly unit: number
}

/**
 * The tooltip: the jurisdiction and its count, following the pointer.
 *
 * Drawn inside the SVG rather than as an HTML overlay so it needs no measured position on the
 * page, and with `pointer-events: none` so it can never come between the pointer and a shape
 * — which is what makes a tooltip flicker along a border. It is hidden from assistive
 * technology because it repeats the shape's own accessible name.
 *
 * Its contents are laid out in CSS pixels and scaled into user units as a whole, so the type
 * stays the same size whether the map is 300 or 600 pixels wide.
 *
 * @param props - Component properties.
 * @returns The tooltip.
 */
function MapTooltip({ name, phrase, anchor, unit }: MapTooltipProps): JSX.Element {
  const width = Math.max(name.length, phrase.length) * TOOLTIP_CHARACTER_WIDTH + TOOLTIP_PADDING * 2
  const offset = TOOLTIP_OFFSET * unit

  let x = anchor.x + offset
  if (x + width * unit > MAP_VIEW_BOX.width) x = anchor.x - offset - width * unit
  x = Math.max(0, Math.min(x, MAP_VIEW_BOX.width - width * unit))

  let y = anchor.y - offset - TOOLTIP_HEIGHT * unit
  if (y < 0) y = anchor.y + offset
  y = Math.max(0, Math.min(y, MAP_VIEW_BOX.height - TOOLTIP_HEIGHT * unit))

  /**
   * Trim a number to two decimals, so the transform does not carry sixteen digits of noise.
   *
   * @param value - The number.
   * @returns Its shortest useful form.
   */
  const short = (value: number): string => String(Math.round(value * 100) / 100)

  return (
    <g
      aria-hidden="true"
      className="pointer-events-none"
      transform={`translate(${short(x)} ${short(y)}) scale(${short(unit)})`}
    >
      <rect
        x={0}
        y={0}
        width={width}
        height={TOOLTIP_HEIGHT}
        rx={4}
        className="fill-plt-accent-deep stroke-plt-accent-deep"
        strokeWidth={1}
      />
      <text x={TOOLTIP_PADDING} y={17} className="fill-plt-inverse text-xs font-semibold">
        {name}
      </text>
      <text x={TOOLTIP_PADDING} y={33} className="fill-plt-inverse-muted text-xs">
        {phrase}
      </text>
    </g>
  )
}
