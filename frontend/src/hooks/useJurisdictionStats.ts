/**
 * Load the per-jurisdiction case counts the map renders.
 *
 * One request, on mount, and nothing after it: `GET /api/stats/jurisdictions` answers for
 * every jurisdiction at once (`docs/architecture.md` section 5.1), so hovering or focusing a
 * shape resolves against data already in memory rather than causing a request. The three
 * states the map has to render — loading, ready and error — are one discriminated union, so
 * a component cannot forget the error branch.
 *
 * The counts are keyed by `map_feature_id` rather than by `code` because that is the field
 * the map resolves a shape against (`docs/architecture.md` section 3); the two agree today,
 * and the map does not need to care if they ever stop agreeing.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError, getJurisdictionStats } from '@/api/client'
import type { JurisdictionStat } from '@/types/api'

/** What the map knows at a given moment. */
export type JurisdictionStatsState =
  | { readonly status: 'loading' }
  | { readonly status: 'ready'; readonly stats: readonly JurisdictionStat[] }
  | { readonly status: 'error'; readonly error: ApiError }

/** Return value of {@link useJurisdictionStats}. */
export interface UseJurisdictionStatsResult {
  /** Current state of the payload. */
  readonly state: JurisdictionStatsState
  /** The jurisdictions by `map_feature_id`; empty while loading and after a failure. */
  readonly byFeatureId: ReadonlyMap<string, JurisdictionStat>
  /** Request the payload again, for the retry control on the error state. */
  readonly reload: () => void
}

/**
 * Subscribe to the map payload.
 *
 * @returns The current state, the counts indexed for lookup, and a reload callback.
 */
export function useJurisdictionStats(): UseJurisdictionStatsResult {
  const [state, setState] = useState<JurisdictionStatsState>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)

  const reload = useCallback((): void => {
    setState({ status: 'loading' })
    setAttempt((previous) => previous + 1)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    let active = true

    setState({ status: 'loading' })

    getJurisdictionStats(controller.signal)
      .then((stats) => {
        if (active) setState({ status: 'ready', stats })
      })
      .catch((error: unknown) => {
        // A cancellation is this component's own doing; it is not something to report.
        if (!active || controller.signal.aborted) return
        setState({
          status: 'error',
          error:
            error instanceof ApiError
              ? error
              : new ApiError('The case counts could not be loaded.', 0, 'unknown_error', {}),
        })
      })

    return (): void => {
      active = false
      controller.abort()
    }
  }, [attempt])

  const byFeatureId = useMemo((): ReadonlyMap<string, JurisdictionStat> => {
    if (state.status !== 'ready') return new Map<string, JurisdictionStat>()
    return new Map(state.stats.map((entry) => [entry.map_feature_id, entry]))
  }, [state])

  return { state, byFeatureId, reload }
}
