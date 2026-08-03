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
