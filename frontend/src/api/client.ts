/**
 * Typed fetch wrapper.
 *
 * This module is the single place the API base URL is read and the only place `fetch` is
 * called (`docs/architecture.md` section 6). Components and hooks call the helpers here so
 * that timeouts, cancellation and the uniform error envelope are handled in one place.
 */

import type {
  ApiErrorEnvelope,
  CaseRecord,
  CaseSummary,
  ExclusionsResponse,
  ExportFormat,
  FilterFacets,
  HealthResponse,
  JurisdictionStat,
  Paginated,
  SubscriptionResponse,
} from '@/types/api'

/** Base URL every request is resolved against. Empty means "the site's own origin". */
const API_BASE_URL: string = (import.meta.env.VITE_API_BASE_URL ?? '/api').replace(/\/+$/, '')

/** Per-request timeout in milliseconds. */
const API_TIMEOUT_MS: number = Number.parseInt(import.meta.env.VITE_API_TIMEOUT_MS ?? '15000', 10)

/** Query parameter values the client knows how to serialise. */
export type QueryValue = string | number | boolean | ReadonlyArray<string | number> | undefined

/** Options accepted by {@link request}. */
export interface RequestOptions {
  /** Query parameters. `undefined` values are dropped; arrays repeat the key. */
  query?: Record<string, QueryValue>
  /** Caller-supplied abort signal, combined with the client's own timeout. */
  signal?: AbortSignal
  /** HTTP method. The public API is read-only, so this defaults to `GET`. */
  method?: 'GET' | 'POST'
  /** JSON request body, for the endpoints that take one. */
  body?: unknown
}

/**
 * An error returned by the API, or a transport failure dressed in the same shape.
 *
 * `status` is `0` when the request never reached the server (network failure, timeout or
 * cancellation), which lets callers distinguish "offline" from "rejected".
 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown>

  constructor(message: string, status: number, code: string, details: Record<string, unknown>) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }

  /** Whether the request failed before a response was received. */
  get isNetworkError(): boolean {
    return this.status === 0
  }
}

/**
 * Serialise query parameters, dropping empties and repeating keys for array values.
 *
 * @param query - Parameters to serialise.
 * @returns A query string including the leading `?`, or an empty string.
 */
function buildQueryString(query: Record<string, QueryValue> | undefined): string {
  if (query === undefined) return ''

  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === '') continue
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, String(item))
    } else {
      params.append(key, String(value))
    }
  }

  const serialised = params.toString()
  return serialised === '' ? '' : `?${serialised}`
}

/**
 * Turn a non-OK response into an {@link ApiError}, using the API's error envelope when the
 * body carries one.
 *
 * @param response - The failed response.
 * @returns The error to throw.
 */
async function toApiError(response: Response): Promise<ApiError> {
  let code = 'http_error'
  let message = `Request failed with status ${response.status}`
  let details: Record<string, unknown> = {}

  try {
    const body: unknown = await response.json()
    const envelope = body as Partial<ApiErrorEnvelope>
    if (envelope.error !== undefined) {
      code = envelope.error.code
      message = envelope.error.message
      details = envelope.error.details ?? {}
    }
  } catch {
    // A non-JSON body (a proxy error page, say) leaves the defaults in place.
  }

  return new ApiError(message, response.status, code, details)
}

/**
 * Perform a request against the PLT API.
 *
 * @typeParam T - Expected response type. The caller asserts the shape; the endpoint
 *   contract lives in `src/types/api.ts`.
 * @param path - Path relative to the API base URL, e.g. `/cases/latest`.
 * @param options - Query parameters, method, body and an optional abort signal.
 * @returns The parsed JSON response.
 * @throws {ApiError} If the request fails, times out, or the API returns a non-2xx status.
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { query, signal, method = 'GET', body } = options
  const url = `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}${buildQueryString(query)}`

  const timeout = new AbortController()
  const timer = setTimeout(() => {
    timeout.abort()
  }, API_TIMEOUT_MS)
  const onExternalAbort = (): void => {
    timeout.abort()
  }
  signal?.addEventListener('abort', onExternalAbort)

  try {
    const response = await fetch(url, {
      method,
      signal: timeout.signal,
      headers: {
        Accept: 'application/json',
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })

    if (!response.ok) throw await toApiError(response)
    if (response.status === 204) return undefined as T

    return (await response.json()) as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    const aborted = error instanceof DOMException && error.name === 'AbortError'
    throw new ApiError(
      aborted ? 'The request timed out or was cancelled.' : 'The API could not be reached.',
      0,
      aborted ? 'request_aborted' : 'network_error',
      {},
    )
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onExternalAbort)
  }
}

/**
 * Fetch API liveness.
 *
 * @param signal - Optional abort signal.
 * @returns The health payload.
 */
export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>('/health', signal === undefined ? {} : { signal })
}

/**
 * Build an absolute-or-rooted URL for an API endpoint without requesting it.
 *
 * Needed for downloads: an export has to be an ordinary link so the browser handles the
 * `Content-Disposition` and writes the file itself, which `fetch` cannot do. Keeping the URL
 * construction here means the base URL is still read in exactly one module
 * (`docs/architecture.md` section 6).
 *
 * @param path - Path relative to the API base URL.
 * @param query - Query parameters, serialised as they are for {@link request}.
 * @returns The URL to put in an `href`.
 */
export function buildApiUrl(path: string, query?: Record<string, QueryValue>): string {
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}${buildQueryString(query)}`
}

/**
 * Search, filter and paginate the collection (`GET /api/cases`).
 *
 * @param query - Query parameters from `docs/architecture.md` section 5. `jurisdiction`
 *   repeats for multiple values; empty and `undefined` values are dropped.
 * @param signal - Optional abort signal, so a superseded search is cancelled.
 * @returns One page of results with the totals a paginator needs.
 * @throws {ApiError} If the request fails or the API rejects a parameter.
 */
export function searchCases(
  query: Record<string, QueryValue>,
  signal?: AbortSignal,
): Promise<Paginated<CaseSummary>> {
  return request<Paginated<CaseSummary>>('/cases', signal === undefined ? { query } : { query, signal })
}

/**
 * Fetch one case with its documents, parties and topics
 * (`GET /api/cases/<jurisdiction>/<source_id>`).
 *
 * Both path segments are percent-encoded: a source identifier is an ECLI or CELEX number
 * supplied through the URL bar, so it is untrusted and must not be able to reach outside the
 * path it is meant to address.
 *
 * @param jurisdiction - Jurisdiction code, e.g. `NL`.
 * @param sourceId - ECLI or CELEX identifier.
 * @param signal - Optional abort signal.
 * @returns The case record.
 * @throws {ApiError} With `status` 404 when no such case exists.
 */
export function getCase(
  jurisdiction: string,
  sourceId: string,
  signal?: AbortSignal,
): Promise<CaseRecord> {
  const path = `/cases/${encodeURIComponent(jurisdiction)}/${encodeURIComponent(sourceId)}`
  return request<CaseRecord>(path, signal === undefined ? {} : { signal })
}

/**
 * Fetch the facet values behind the All-cases filters (`GET /api/filters`).
 *
 * @param signal - Optional abort signal.
 * @returns The facet payload.
 * @throws {ApiError} If the request fails.
 */
export function getFilters(signal?: AbortSignal): Promise<FilterFacets> {
  return request<FilterFacets>('/filters', signal === undefined ? {} : { signal })
}

/**
 * Fetch the exclusion criteria of every jurisdiction (`GET /api/exclusions`).
 *
 * Read by the methodology page. Separate from `getFilters` because it answers a different
 * question — what the criterion keeps out, rather than what a reader may filter by — and
 * the filter controls should not pay for it.
 *
 * @param signal - Optional abort signal.
 * @returns The exclusion payload.
 * @throws {ApiError} If the request fails.
 */
export function getExclusions(signal?: AbortSignal): Promise<ExclusionsResponse> {
  return request<ExclusionsResponse>('/exclusions', signal === undefined ? {} : { signal })
}

/**
 * Build the download URL for the current result set (`GET /api/cases/export`).
 *
 * @param query - The active filters, in the same spelling `GET /api/cases` takes. Paging
 *   parameters are the caller's to leave out: an export covers the whole selection.
 * @param format - `csv` or `json`.
 * @returns The URL to put in the download link's `href`.
 */
export function caseExportUrl(query: Record<string, QueryValue>, format: ExportFormat): string {
  return buildApiUrl('/cases/export', { ...query, format })
}

/** Largest `limit` that `GET /api/cases/latest` accepts (`docs/architecture.md` section 5). */
export const LATEST_CASES_MAX_LIMIT = 50

/** How many cases the home sidebar asks for (`docs/architecture.md` section 6). */
export const LATEST_CASES_DEFAULT_LIMIT = 20

/**
 * Narrow one element of a list payload to a {@link CaseSummary}.
 *
 * Server data is `unknown` until something checks it. Casting instead would move a shape
 * mismatch from this function, where it can be reported as an error state, into a component,
 * where it is a blank page. Absent optional fields become `null` so a component never has to
 * distinguish "missing" from "empty".
 *
 * **What is rejected, and what is not.** Only `jurisdiction_code` and `source_id` are
 * required, because `caseDetailPath` builds the route out of exactly those two and a row
 * without them is unrenderable. Every other field of `CaseSummary` may be `null` under
 * `docs/architecture.md` section 5.1 — `title` included, since `case.title` is nullable in
 * the section 3 schema and a metadata-only ECLI or an untitled CELLAR notice will carry
 * none. Dropping such a record would silently shorten the feed: the caller asks for twenty
 * and gets nineteen, with no error to show for it. A missing title is a rendering question,
 * answered by `caseLabel`, not grounds for discarding a case from a research database.
 *
 * @param value - Candidate element from the response body.
 * @returns The normalised case, or `null` when the element is not one.
 */
function toCaseSummary(value: unknown): CaseSummary | null {
  if (typeof value !== 'object' || value === null) return null

  const record = value as Record<string, unknown>
  const jurisdictionCode = record.jurisdiction_code
  const sourceId = record.source_id

  if (typeof jurisdictionCode !== 'string' || jurisdictionCode === '') return null
  if (typeof sourceId !== 'string' || sourceId === '') return null

  return {
    jurisdiction_code: jurisdictionCode,
    jurisdiction_name: typeof record.jurisdiction_name === 'string' ? record.jurisdiction_name : null,
    source_id: sourceId,
    title: typeof record.title === 'string' ? record.title : null,
    court_name: typeof record.court_name === 'string' ? record.court_name : null,
    decision_date: typeof record.decision_date === 'string' ? record.decision_date : null,
  }
}

/**
 * Pull the item array out of a list response.
 *
 * @param payload - Parsed response body.
 * @returns The raw elements.
 * @throws {ApiError} With code `invalid_response` if the body is neither an array nor a
 *   paginated envelope.
 */
function listItems(payload: unknown): readonly unknown[] {
  if (Array.isArray(payload)) return payload
  if (typeof payload === 'object' && payload !== null) {
    const items = (payload as Record<string, unknown>).items
    if (Array.isArray(items)) return items
  }
  throw new ApiError('The API returned a response the tracker could not read.', 0, 'invalid_response', {})
}

/**
 * Fetch the newest cases for the home-page sidebar.
 *
 * `limit` is clamped to the range the endpoint documents, so a caller cannot ask for a page
 * the API will reject.
 *
 * @param limit - How many cases to ask for. Clamped to 1…{@link LATEST_CASES_MAX_LIMIT}.
 * @param signal - Optional abort signal, so an unmounting component cancels the request.
 * @returns The cases, newest first.
 * @throws {ApiError} On a transport failure, a non-2xx status, or an unreadable body.
 */
export async function getLatestCases(
  limit: number = LATEST_CASES_DEFAULT_LIMIT,
  signal?: AbortSignal,
): Promise<CaseSummary[]> {
  const bounded = Math.min(Math.max(Math.trunc(limit), 1), LATEST_CASES_MAX_LIMIT)
  const payload = await request<unknown>('/cases/latest', {
    query: { limit: bounded },
    ...(signal === undefined ? {} : { signal }),
  })

  const items = listItems(payload)
  const cases = items.map(toCaseSummary).filter((entry): entry is CaseSummary => entry !== null)

  // A non-empty list in which nothing is recognisable is a contract mismatch, not an empty
  // feed, and has to be reported as such rather than shown as "no cases yet".
  if (items.length > 0 && cases.length === 0) {
    throw new ApiError('The API returned cases in an unexpected format.', 0, 'invalid_response', {})
  }

  return cases
}

/**
 * Narrow one element of the map payload to a {@link JurisdictionStat}.
 *
 * `docs/architecture.md` section 5.1 fixes this shape exactly, so this is a check rather
 * than a negotiation: an element missing a required field is dropped rather than patched up,
 * because a jurisdiction the map cannot name or count is one it must not draw a number for.
 * Absent values are `null` in that contract and never omitted, which is why
 * `latest_decision_date` is read as "a string or null" and nothing else.
 *
 * **`map_feature_id` is required of the server but not demanded of the response.** The
 * column is `NOT NULL` since revision `0006`, so a null is now a contract violation rather
 * than a legitimate shape — but the answer to a violation is not to discard a jurisdiction
 * that named and counted itself perfectly well. Section 3 makes the fallback exact rather
 * than approximate: the identifier *is* the alpha-2 code for a state and the sentinel `EU`
 * for the Union, which is `code` in both cases. Falling back is therefore never worse than
 * dropping — at worst the id joins no shape, which is what dropping already produced — and
 * it is strictly better in the one case where the two behaviours diverge, an entire payload
 * without the field, where dropping trips the contract-mismatch guard below and throws the
 * whole map instead of degrading to "no cases yet".
 *
 * @param value - Candidate element from the response body.
 * @returns The jurisdiction, or `null` when the element is not one.
 */
function toJurisdictionStat(value: unknown): JurisdictionStat | null {
  if (typeof value !== 'object' || value === null) return null

  const record = value as Record<string, unknown>
  const code = record.code
  const name = record.name
  const mapFeatureId = record.map_feature_id
  const caseCount = record.case_count
  const type = record.type

  if (typeof code !== 'string' || code === '') return null
  if (typeof name !== 'string' || name === '') return null
  if (typeof caseCount !== 'number' || !Number.isFinite(caseCount) || caseCount < 0) return null

  return {
    code,
    name,
    type: type === 'supranational' ? 'supranational' : 'state',
    map_feature_id: typeof mapFeatureId === 'string' && mapFeatureId !== '' ? mapFeatureId : code,
    is_active: record.is_active !== false,
    case_count: Math.trunc(caseCount),
    latest_decision_date:
      typeof record.latest_decision_date === 'string' ? record.latest_decision_date : null,
  }
}

/**
 * Fetch the per-jurisdiction case counts the map renders.
 *
 * One call answers the whole map: section 5.1 fixes the payload as a bare array covering
 * every jurisdiction, zero-case ones included, and the endpoint resolves in a single
 * aggregate query. Nothing here is per-jurisdiction, so hovering a shape never causes a
 * request.
 *
 * @param signal - Optional abort signal, so an unmounting component cancels the request.
 * @returns The jurisdictions, in the order the API returned them.
 * @throws {ApiError} On a transport failure, a non-2xx status, or an unreadable body.
 */
export async function getJurisdictionStats(signal?: AbortSignal): Promise<JurisdictionStat[]> {
  const payload = await request<unknown>('/stats/jurisdictions', signal === undefined ? {} : { signal })

  if (!Array.isArray(payload)) {
    throw new ApiError('The API returned a response the tracker could not read.', 0, 'invalid_response', {})
  }

  const stats = payload
    .map(toJurisdictionStat)
    .filter((entry): entry is JurisdictionStat => entry !== null)

  // A non-empty payload in which nothing is recognisable is a contract mismatch, not an
  // empty database, and has to be reported rather than drawn as a map with no cases on it.
  if (payload.length > 0 && stats.length === 0) {
    throw new ApiError('The API returned jurisdictions in an unexpected format.', 0, 'invalid_response', {})
  }

  return stats
}

/**
 * Ask for email alerts (`POST /api/subscriptions`).
 *
 * The address is sent in a body over `POST`, never in a URL: a query string reaches the
 * browser history, the server log and any `Referer` the page emits, and a subscriber's
 * address is personal data. The endpoint answers the same way whatever it found, so the
 * caller learns nothing about who is on the list and neither does the reader.
 *
 * @param email - The address the reader typed. Validated server-side; the form checks only
 *   enough to give immediate feedback.
 * @param signal - Optional abort signal.
 * @returns The acknowledgement, whose `message` is what the form should show.
 * @throws {ApiError} With code `validation_error` when the address is malformed,
 *   `mail_unavailable` when the message could not be sent, or status 429 when the caller has
 *   submitted too often.
 */
export function subscribe(email: string, signal?: AbortSignal): Promise<SubscriptionResponse> {
  return request<SubscriptionResponse>('/subscriptions', {
    method: 'POST',
    body: { email },
    ...(signal === undefined ? {} : { signal }),
  })
}

/**
 * Confirm a subscription with the token from the emailed link
 * (`POST /api/subscriptions/confirm`).
 *
 * @param token - The token from the link's query string.
 * @param signal - Optional abort signal.
 * @returns The confirmation.
 * @throws {ApiError} With code `invalid_token` when the link has expired or is not one this
 *   deployment issued.
 */
export function confirmSubscription(
  token: string,
  signal?: AbortSignal,
): Promise<SubscriptionResponse> {
  return request<SubscriptionResponse>('/subscriptions/confirm', {
    method: 'POST',
    body: { token },
    ...(signal === undefined ? {} : { signal }),
  })
}

/**
 * End a subscription (`POST /api/subscriptions/unsubscribe`).
 *
 * `POST`, not `GET`, although the link in an email is an ordinary link: the page behind that
 * link performs this request, so a mailbox provider's link scanner cannot unsubscribe a
 * reader merely by following the URL to check it.
 *
 * @param token - The token from the link's query string.
 * @param signal - Optional abort signal.
 * @returns The acknowledgement.
 * @throws {ApiError} With code `invalid_token` when the token does not verify.
 */
export function unsubscribe(token: string, signal?: AbortSignal): Promise<SubscriptionResponse> {
  return request<SubscriptionResponse>('/subscriptions/unsubscribe', {
    method: 'POST',
    body: { token },
    ...(signal === undefined ? {} : { signal }),
  })
}

/**
 * Ask for an unsubscribe link by email (`POST /api/subscriptions/unsubscribe-link`).
 *
 * The route out for a reader who no longer has one of the emails. The link goes to the
 * address itself, so nobody can cancel somebody else's subscription, and the answer is the
 * same whether or not the address is on the list.
 *
 * @param email - The address to send the link to.
 * @param signal - Optional abort signal.
 * @returns The acknowledgement.
 * @throws {ApiError} As {@link subscribe}.
 */
export function requestUnsubscribeLink(
  email: string,
  signal?: AbortSignal,
): Promise<SubscriptionResponse> {
  return request<SubscriptionResponse>('/subscriptions/unsubscribe-link', {
    method: 'POST',
    body: { email },
    ...(signal === undefined ? {} : { signal }),
  })
}

/** The configured API base URL, exported for diagnostics and tests. */
export const apiBaseUrl: string = API_BASE_URL
