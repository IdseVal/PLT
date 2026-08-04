/**
 * Load the newest cases for the home-page sidebar.
 *
 * The hook owns the three states the sidebar has to render — loading, ready (possibly
 * empty) and error — as one discriminated union, so a component cannot show a spinner and a
 * list at the same time or forget the error branch. Every request carries an `AbortSignal`,
 * so unmounting or reloading cancels the one in flight instead of setting state on a
 * component that is gone.
 */

import { useCallback, useEffect, useState } from 'react'

import { ApiError, LATEST_CASES_DEFAULT_LIMIT, getLatestCases } from '@/api/client'
import type { CaseSummary } from '@/types/api'

/** What the sidebar knows at a given moment. */
export type LatestCasesState =
  | { readonly status: 'loading' }
  | { readonly status: 'ready'; readonly cases: readonly CaseSummary[] }
  | { readonly status: 'error'; readonly error: ApiError }

/** Return value of {@link useLatestCases}. */
export interface UseLatestCasesResult {
  /** Current state of the feed. */
  readonly state: LatestCasesState
  /** Request the feed again, for the retry control on the error state. */
  readonly reload: () => void
}

/**
 * Subscribe to the latest-cases feed.
 *
 * @param limit - How many cases to request. Clamped by the client to the endpoint's maximum.
 * @returns The current state and a reload callback.
 */
export function useLatestCases(limit: number = LATEST_CASES_DEFAULT_LIMIT): UseLatestCasesResult {
  const [state, setState] = useState<LatestCasesState>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)

  const reload = useCallback((): void => {
    setState({ status: 'loading' })
    setAttempt((previous) => previous + 1)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    let active = true

    setState({ status: 'loading' })

    getLatestCases(limit, controller.signal)
      .then((cases) => {
        if (active) setState({ status: 'ready', cases })
      })
      .catch((error: unknown) => {
        // A cancellation is this component's own doing; it is not something to report.
        if (!active || controller.signal.aborted) return
        setState({
          status: 'error',
          error:
            error instanceof ApiError
              ? error
              : new ApiError('The latest cases could not be loaded.', 0, 'unknown_error', {}),
        })
      })

    return (): void => {
      active = false
      controller.abort()
    }
  }, [limit, attempt])

  return { state, reload }
}
