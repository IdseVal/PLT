/**
 * Link helpers shared by the content renderers and the case listings.
 */

/** Link protocols the application will emit in an `href`. Anything else is dropped. */
const SAFE_PROTOCOLS: readonly string[] = ['https:', 'http:', 'mailto:']

/**
 * Whether an absolute link target is safe to render.
 *
 * Static copy is authored in this repository rather than supplied by users, but rendering
 * an unchecked `href` is how `javascript:` and `data:` URLs get into a page, so the check is
 * made anyway. Relative targets are rejected: application routes go through `to`, which
 * react-router resolves, not through `href`.
 *
 * @param href - Candidate target.
 * @returns `true` when the target is absolute and uses an allowed protocol.
 */
export function isSafeHref(href: string): boolean {
  try {
    return SAFE_PROTOCOLS.includes(new URL(href).protocol)
  } catch {
    return false
  }
}

/**
 * Route of a case detail page.
 *
 * Both segments come from the API and are percent-encoded before going into the path: an
 * ECLI is full of colons, and neither identifier may be trusted to be URL-safe just because
 * it usually is. The shape of the route is fixed by `docs/architecture.md` section 6.
 *
 * @param jurisdictionCode - Jurisdiction code, e.g. `NL` or `EU`.
 * @param sourceId - Source identifier: an ECLI or a CELEX number.
 * @returns An application-relative path for react-router's `to`.
 */
export function caseDetailPath(jurisdictionCode: string, sourceId: string): string {
  return `/cases/${encodeURIComponent(jurisdictionCode)}/${encodeURIComponent(sourceId)}`
}

/**
 * Route of the all-cases page filtered to one jurisdiction.
 *
 * `jurisdiction` is the repeatable filter of `GET /api/cases` (`docs/architecture.md`
 * section 5), which the all-cases page reads from the query string. The code is
 * percent-encoded for the same reason as above: it comes from the API, and "it is always two
 * letters" is an assumption, not a guarantee.
 *
 * @param jurisdictionCode - Jurisdiction code, e.g. `NL` or `EU`.
 * @returns An application-relative path for react-router's `to`.
 */
export function jurisdictionCasesPath(jurisdictionCode: string): string {
  return `/cases?jurisdiction=${encodeURIComponent(jurisdictionCode)}`
}
