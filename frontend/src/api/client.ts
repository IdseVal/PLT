/**
 * Typed fetch wrapper.
 *
 * This module is the single place the API base URL is read and the only place `fetch` is
 * called (`docs/architecture.md` section 6). Components and hooks call the helpers here so
 * that timeouts, cancellation and the uniform error envelope are handled in one place.
 */

import type { ApiErrorEnvelope, HealthResponse } from '@/types/api'

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

/** The configured API base URL, exported for diagnostics and tests. */
export const apiBaseUrl: string = API_BASE_URL
