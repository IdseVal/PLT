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
 * Case-law payloads.
 *
 * `docs/architecture.md` section 5 fixes the endpoints, their query parameters and the error
 * envelope, but not the field-by-field shape of a case response. The types below therefore
 * mirror the database contract in section 3 — the same column names, in the same
 * lower-case-with-underscores spelling the API is required to expose for enumerated values —
 * so the wire format needs no translation layer. **They are the frontend's reading of the
 * contract, not the contract itself**: when the API issue (#10) lands, section 5 should gain
 * an explicit response table and these interfaces should be reconciled against it.
 *
 * Everything here is data the pipeline copied out of a court's publication system, so every
 * string in it is untrusted input. It is rendered as text, never as markup — see
 * `src/utils/caseText.ts` — and any URL is checked against `isSafeHref` before it reaches an
 * `href`.
 */

/** The court that issued a decision (`court` table, section 3). */
export interface CourtRef {
  id: number
  name: string
  /** Instance or level, e.g. `supreme`. Free text from the source vocabulary. */
  level?: string | null
  abbreviation?: string | null
}

/** One topic assigned to a case (`topic` table, section 3, classification label 6). */
export interface TopicRef {
  slug: string
  label: string
}

/** A litigating party (`party` table, section 3, classification label 4). */
export interface PartyRef {
  name: string
  /** `applicant` | `defendant` | `intervener` | `other`, persisted as the enum value. */
  role: string
  party_type?: string | null
}

/** Kinds of document a case can carry (`case_document.doc_type`, section 3). */
export type CaseDocumentType = 'judgment' | 'opinion' | 'summary' | 'attachment' | 'other'

/**
 * One full text or attachment belonging to a case.
 *
 * `raw_payload` is deliberately absent: the frontend never renders the verbatim source
 * response, only the extracted plain text.
 */
export interface CaseDocumentRef {
  id: number
  /** ISO 639-1 code of the language this document is written in. */
  language?: string | null
  doc_type: CaseDocumentType
  /** Source format the text was extracted from, e.g. `xml` or `html`. */
  format?: string | null
  /** Plain text of the document. Untrusted: rendered as text only. */
  full_text?: string | null
  source_url?: string | null
}

/** A case as it appears in a list: enough to judge relevance, not the full text. */
export interface CaseSummary {
  id: number
  jurisdiction_code: string
  jurisdiction_name?: string | null
  /** ECLI or CELEX identifier; unique within the jurisdiction. */
  source_id: string
  source_system?: string | null
  title?: string | null
  abstract?: string | null
  /** ISO 8601 calendar date, e.g. `2024-03-05`. */
  decision_date?: string | null
  language?: string | null
  /** `public` | `private` | `criminal` (section 2.2 label 2). */
  law_domain?: string | null
  law_subfield?: string | null
  court?: CourtRef | null
  topics?: TopicRef[]
  /** The case's page in the court's own publication system. */
  source_url?: string | null
}

/** A single case with everything the detail page shows. */
export interface CaseRecord extends CaseSummary {
  filing_date?: string | null
  publication_date?: string | null
  case_numbers?: string[]
  procedure_type?: string | null
  outcome?: string | null
  parties?: PartyRef[]
  documents?: CaseDocumentRef[]
}

/**
 * Facet values behind the All-cases filters (`GET /api/filters`).
 *
 * Mirrors `plt.db.repositories.FacetValues`, which returns only values that occur on a
 * published case, so the UI cannot offer a filter that yields nothing.
 */
export interface FilterFacets {
  jurisdictions: { code: string; name: string }[]
  courts: { id: number; name: string }[]
  law_domains: string[]
  law_subfields: string[]
  languages: string[]
  topics: TopicRef[]
  /** ISO dates bounding the collection, for the date-range inputs. */
  earliest_decision_date?: string | null
  latest_decision_date?: string | null
}

/** Formats offered by `GET /api/cases/export`. */
export type ExportFormat = 'csv' | 'json'
