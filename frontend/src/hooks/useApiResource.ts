/**
 * Load one thing from the API into component state.
 *
 * Every data-driven page needs the same four behaviours, and getting any of them wrong is
 * subtle enough to be worth writing once: cancel the request when the component unmounts or
 * the parameters change, ignore the response of a request that has been superseded, keep the
 * previous data on screen while the next page loads instead of flashing an empty layout, and
 * surface the failure as an `ApiError` the page can respond to (a 404 is a not-found page; a
 * network failure is a retry).
 *
 * The hook calls `src/api/client.ts` through the loader it is given and never `fetch`
 * itself, per `docs/architecture.md` section 6.
 */

import { useEffect, useState } from 'react'

import { ApiError } from '@/api/client'

/** What a component knows about the resource it asked for. */
export interface ApiResourceState<T> {
  /** The most recent successful response, or `null` before the first one arrives. */
  readonly data: T | null
  /** The error that ended the most recent attempt, or `null`. */
  readonly error: ApiError | null
  /** Whether a request is in flight. `data` may still hold the previous response. */
  readonly isLoading: boolean
}

/**
 * Run a loader whenever it changes, and report its outcome.
 *
 * @typeParam T - Type of the resource.
 * @param loader - Function that performs the request. Wrap it in `useCallback` so it is
 *   stable: the hook re-runs whenever this function's identity changes, which is what makes
 *   the caller's dependency list the refetch trigger.
 * @returns The current state of the resource.
 */
export function useApiResource<T>(
  loader: (signal: AbortSignal) => Promise<T>,
): ApiResourceState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    const controller = new AbortController()
    let active = true

    setIsLoading(true)

    loader(controller.signal)
      .then((result) => {
        if (!active) return
        setData(result)
        setError(null)
      })
      .catch((cause: unknown) => {
        // A cancelled request is not a failure: the component moved on, or unmounted.
        if (!active || controller.signal.aborted) return
        setError(
          cause instanceof ApiError
            ? cause
            : new ApiError('The request could not be completed.', 0, 'unexpected_error', {}),
        )
      })
      .finally(() => {
        if (active) setIsLoading(false)
      })

    return (): void => {
      active = false
      controller.abort()
    }
  }, [loader])

  return { data, error, isLoading }
}
