/**
 * Link helpers shared by the content renderers.
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
