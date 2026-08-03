/**
 * Keep the browser document title in step with the route.
 *
 * A single-page application does not change its title on navigation by itself, which leaves
 * screen-reader and browser-history users with the same title on every page (WCAG 2.4.2,
 * "Page Titled"). Every route-level component calls this hook.
 */

import { useEffect } from 'react'

/** Suffix appended to every page title. */
const SITE_NAME = 'Pesticide Litigation Tracker'

/**
 * Set `document.title` for as long as the calling component is mounted.
 *
 * @param title - Page-specific part of the title. When empty or equal to the site name,
 *   the site name alone is used.
 */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    const trimmed = title.trim()
    document.title = trimmed === '' || trimmed === SITE_NAME ? SITE_NAME : `${trimmed} · ${SITE_NAME}`
  }, [title])
}
