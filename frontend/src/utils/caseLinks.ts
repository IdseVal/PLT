/**
 * Addressing a case within the site.
 *
 * A case is identified by its jurisdiction and its source identifier — an ECLI or a CELEX
 * number — rather than by a database id, so the route in `docs/architecture.md` section 6 is
 * `/cases/:jurisdiction/:sourceId`. Both segments are percent-encoded here: an ECLI contains
 * colons, and the identifier ultimately comes from data this project did not author, so it
 * must not be able to reach outside the path it addresses.
 */

import { cleanInlineText } from '@/utils/caseText'
import type { CaseSummary } from '@/types/api'

/** Shown when a case has neither a title nor a usable identifier. */
const UNTITLED = 'Untitled case'

/**
 * The in-site path of a case.
 *
 * @param item - The case, or the jurisdiction and identifier on their own.
 * @returns The route path, safe to hand to react-router's `to`.
 */
export function caseHref(item: Pick<CaseSummary, 'jurisdiction_code' | 'source_id'>): string {
  return `/cases/${encodeURIComponent(item.jurisdiction_code)}/${encodeURIComponent(item.source_id)}`
}

/**
 * The name a case is listed under.
 *
 * Not every source publishes a title — Rechtspraak often does not — so the identifier is the
 * fallback, and a link is never left without an accessible name.
 *
 * @param item - The case.
 * @returns A cleaned, non-empty label.
 */
export function caseLabel(item: Pick<CaseSummary, 'title' | 'source_id'>): string {
  return cleanInlineText(item.title) || cleanInlineText(item.source_id) || UNTITLED
}
