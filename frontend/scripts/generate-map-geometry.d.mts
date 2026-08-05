/**
 * Types for the parts of `generate-map-geometry.mjs` that are read from TypeScript.
 *
 * The generator is a plain Node script with no dependencies and no build step, so it stays
 * JavaScript; this states the shape of the one table `tests/mapGeometry.test.ts` imports from
 * it. Nothing in `src/` imports the generator — it never reaches the browser bundle.
 */

/** How the generator draws one jurisdiction Annex 2 names. */
export interface JurisdictionGeometrySource {
  /** `map_feature_id`: the ISO 3166-1 alpha-2 code the shape is published under. */
  readonly code: string
  /** The country's ISO 3166-1 numeric code, which is its id in the Natural Earth data. */
  readonly source: string
}

/** The generator's name-to-geometry table, keyed by the jurisdiction name Annex 2 uses. */
export declare const GEOMETRY_BY_JURISDICTION: Readonly<Record<string, JurisdictionGeometrySource>>
