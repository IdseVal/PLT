/**
 * Types shared with the HTTP API contract in `docs/architecture.md` section 5.
 *
 * These describe the wire format only. Response types for cases, statistics and facets are
 * added by the issues that implement those endpoints; the scaffold defines the envelope
 * every endpoint shares plus the health payload, which exists today.
 */

/** The uniform error envelope every failing endpoint returns. */
export interface ApiErrorEnvelope {
  error: {
    /** Stable, machine-readable code, e.g. `validation_error`. */
    code: string
    /** Human-readable message, safe to show to a user. */
    message: string
    /** Optional structured context, e.g. the parameter that failed validation. */
    details?: Record<string, unknown>
  }
}

/** Shape shared by every paginated list endpoint. */
export interface Paginated<T> {
  items: T[]
  page: number
  page_size: number
  total: number
}

/** Payload of `GET /api/health`. */
export interface HealthResponse {
  status: 'ok'
  service: string
  version: string
  /** Last successful ingest per jurisdiction code; empty until the pipeline runs. */
  ingest: Record<string, string>
}

/** Sort orders accepted by `GET /api/cases`. */
export type CaseSort = 'date_desc' | 'date_asc' | 'relevance'

/**
 * One case as a list endpoint returns it: enough to render a row and link to the detail
 * page, and nothing more.
 *
 * `docs/architecture.md` section 5 fixes the endpoints and their query parameters but not
 * the field names of a case payload, so the names here follow the `case` table columns in
 * section 3, which the API exposes. `jurisdiction_code` and `source_id` are the two path
 * parameters of `GET /api/cases/<jurisdiction>/<source_id>`, so the detail link is safe to
 * build from them.
 *
 * Everything the API cannot supply is `null` rather than absent: a source that does not name
 * a court or date must not turn into `undefined` halfway down a component.
 *
 * **Every string field is untrusted court text.** Render it as text — never through
 * `dangerouslySetInnerHTML`, and never into an `href`.
 */
export interface CaseSummary {
  /** Jurisdiction code, `NL` or `EU` (`jurisdiction.code`). */
  jurisdiction_code: string
  /** Human-readable jurisdiction name, when the API joins it in. */
  jurisdiction_name: string | null
  /** Source identifier: an ECLI or a CELEX number, unique within the jurisdiction. */
  source_id: string
  /** Case title, as published by the court. */
  title: string
  /** Name of the deciding court or instance. */
  court_name: string | null
  /** Decision date as an ISO-8601 string (`YYYY-MM-DD` or a full timestamp). */
  decision_date: string | null
}

/**
 * One jurisdiction's entry in the map payload.
 *
 * Fixed field by field by `docs/architecture.md` section 5.1. Two rules from there shape
 * this type: a value the API has none of is **present and `null`**, never omitted — which is
 * why `latest_decision_date` is `string | null` and not optional — and the endpoint includes
 * jurisdictions whose `case_count` is `0`, so the map can render intended coverage rather
 * than only what has been ingested.
 */
export interface JurisdictionStat {
  /** Jurisdiction code (`jurisdiction.code`), e.g. `NL` or `EU`. */
  code: string
  /** Human-readable name, e.g. `Netherlands`. */
  name: string
  /** Whether the jurisdiction is a state or the Union itself. */
  type: 'state' | 'supranational'
  /**
   * The identifier the map resolves a jurisdiction against (`docs/architecture.md` section
   * 3): the ISO 3166-1 alpha-2 code for a state, and the sentinel `EU` for the Union.
   */
  map_feature_id: string
  /** Whether the jurisdiction is being ingested. */
  is_active: boolean
  /** How many published cases the tracker holds. Zero is a value, not an absence. */
  case_count: number
  /** Newest decision date held, `YYYY-MM-DD`, or `null` when there are no cases. */
  latest_decision_date: string | null
}

/** Payload of `GET /api/stats/jurisdictions`: a bare array, ordered by code (section 5.1). */
export type JurisdictionStatsResponse = JurisdictionStat[]

/**
 * Payload of `GET /api/cases/latest?limit=20`.
 *
 * Section 5 says every list endpoint paginates but does not spell out whether the sidebar
 * feed carries the pagination envelope or is a bare array, so the client accepts both and
 * normalises. See the note in the pull request for #12.
 */
export type LatestCasesResponse = Paginated<CaseSummary> | CaseSummary[]
