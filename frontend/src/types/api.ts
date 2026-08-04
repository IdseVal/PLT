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

/**
 * Shape shared by every paginated list endpoint (`docs/architecture.md` section 5.1).
 *
 * `page_count` and `has_next` are the server's own arithmetic. A paginator that recomputes
 * them from `total` and `page_size` can disagree with the API about where the last page is;
 * these are optional here only because the envelope predates section 5.1, and a caller
 * should prefer them and fall back rather than ignore them.
 */
export interface Paginated<T> {
  items: T[]
  page: number
  page_size: number
  total: number
  page_count?: number
  has_next?: boolean
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
 * Case-law payloads, as `docs/architecture.md` section 5.1 fixes them.
 *
 * Section 5.1 is the authority for every field below: names, nesting and nullability. Two
 * of its rules shape these declarations.
 *
 * **A field with no value is present and `null`, never omitted.** So the code reads `null`,
 * not `undefined`. Fields are nevertheless declared optional where the home-page sidebar
 * (#12) builds a `CaseSummary` from a narrower normaliser: one type has to satisfy both the
 * full search payload and that six-field object, and widening is the only way to do it
 * without rewriting a page this issue does not own. Read them as `value ?? null`.
 *
 * **Names are flat, not nested** — `court_name` and `court_id` rather than `court: {...}` —
 * so the JSON, the JSON-Lines export and the CSV export all name a field the same way.
 *
 * Everything here is data the pipeline copied out of a court's publication system, so every
 * string in it is untrusted input. It is rendered as text, never as markup — see
 * `src/utils/caseText.ts` — and any URL is checked against `isSafeHref` before it reaches an
 * `href`.
 */

/** One topic assigned to a case (section 5.1, classification label 6 of §2.2). */
export interface TopicRef {
  slug: string
  label: string
  /** Classifier confidence, `null` for a manual assignment. */
  confidence?: number | null
  /** How the topic was assigned, e.g. `manual` or `classifier`. */
  assigned_by?: string | null
}

/** A litigating party (section 5.1, classification label 4 of §2.2). */
export interface PartyRef {
  id?: number
  name: string
  /** `applicant` | `defendant` | `intervener` | `other`, persisted as the enum value. */
  role: string
  party_type?: string | null
  /** Position within its role, as the source listed the parties. */
  ordinal?: number | null
}

/** Kinds of document a case can carry (`case_document.doc_type`, section 3). */
export type CaseDocumentType = 'judgment' | 'opinion' | 'summary' | 'attachment' | 'other'

/**
 * One full text or attachment belonging to a case (section 5.1).
 *
 * `raw_payload` is deliberately absent, and section 5.1 states it is never on the wire: it
 * is the verbatim source response, can be megabytes of markup, and the frontend renders only
 * the extracted plain text.
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
  byte_size?: number | null
  /** ISO 8601 timestamp of the fetch that produced this text. */
  retrieved_at?: string | null
}

/** A term the keyword filter matched on a case (section 5.1). */
export interface KeywordMatchRef {
  term_id: string
  term?: string | null
  list_version?: string | null
  /** Which field the term was found in, e.g. `title` or `full_text`. */
  field?: string | null
  weight_applied?: number | null
  match_count?: number | null
  /** Untrusted: an excerpt of the judgment around the match. */
  snippet?: string | null
}

/** An instrument or judgment a case cites (section 5.1). */
export interface CitationRef {
  target_identifier: string
  /** Identifier scheme, e.g. `celex` or `ecli`. */
  target_scheme?: string | null
  citation_type?: string | null
  target_title?: string | null
  target_url?: string | null
}

/**
 * One case as a list endpoint returns it: `CaseSummary` in section 5.1.
 *
 * `jurisdiction_code` and `source_id` are the two path parameters of
 * `GET /api/cases/<jurisdiction>/<source_id>`, so the detail link is safe to build from
 * them. `court_id` is carried beside `court_name` because the `court` filter takes an id,
 * so a card can link to the rest of a court's cases without resolving the name first.
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

  /** Database identifier. */
  id?: number
  /** Connector the case came from, e.g. `rechtspraak`. */
  source_system?: string | null
  /** Id of the deciding court, the value the `court` filter takes. */
  court_id?: number | null
  abstract?: string | null
  publication_date?: string | null
  /** The court's own case numbers, which are not the ECLI. */
  case_numbers?: string[] | null
  /** ISO 639-1 code of the language the case was issued in. */
  language?: string | null
  /** `public` | `private` | `criminal` | `other` (section 2.2 label 2). */
  law_domain?: string | null
  law_subfield?: string | null
  procedure_type?: string | null
  outcome?: string | null
  /** The case's page in the court's own publication system. */
  source_url?: string | null
  /**
   * Topics assigned to the case.
   *
   * Section 5.1 lists `topics` under `CaseDetail` rather than `CaseSummary`; it is declared
   * here because a result card shows them when they are present and simply omits them when
   * they are not.
   */
  topics?: TopicRef[]
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
 * A single case: `CaseDetail` in section 5.1, every summary field plus the lists.
 *
 * Named `CaseRecord` rather than `CaseDetail` only because `CaseDetail` is the route
 * component that renders it.
 */
export interface CaseRecord extends CaseSummary {
  filing_date?: string | null
  /** How many times the source document has changed since it was first seen. */
  revision?: number | null
  /** ISO 8601 timestamps of the tracker's own handling of the case. */
  first_seen_at?: string | null
  last_seen_at?: string | null
  updated_at?: string | null
  documents?: CaseDocumentRef[]
  parties?: PartyRef[]
  keyword_matches?: KeywordMatchRef[]
  citations?: CitationRef[]
}

/**
 * Facet values behind the All-cases filters (`GET /api/filters`, section 5.1).
 *
 * Only values that occur on a published case are returned, so the UI cannot offer a filter
 * that yields nothing. The payload also carries the bounds the server enforces, so the
 * filter UI reads them rather than repeating them.
 */
export interface FilterFacets {
  jurisdictions: { code: string; name: string }[]
  courts: { id: number; name: string }[]
  topics: TopicRef[]
  law_domains: string[]
  law_subfields: string[]
  languages: string[]
  sorts?: string[]
  export_formats?: string[]
  /** Decision dates bounding the collection, for the date-range inputs. */
  decision_date_range?: { from: string | null; to: string | null }
  page_size_default?: number
  page_size_max?: number
  latest_limit_max?: number
}

/** Formats offered by `GET /api/cases/export` (section 5.1): CSV, or JSON Lines. */
export type ExportFormat = 'csv' | 'jsonl'

/**
 * Payload of `GET /api/cases/latest?limit=20`.
 *
 * Section 5.1 has since pinned this down: the feed is `{items, limit}`, an object and never
 * a bare array. The union is kept for now because `getLatestCases` still normalises both
 * and #12 owns that code; the array arm is dead once the contract is relied on.
 */
export type LatestCasesResponse = Paginated<CaseSummary> | CaseSummary[]
