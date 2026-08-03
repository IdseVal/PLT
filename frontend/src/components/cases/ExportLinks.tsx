/**
 * Download the current selection (`GET /api/cases/export`).
 *
 * Core document section 2.1 point 5: users may download the available case law together with
 * its metadata. The export carries **the active filters and not the page** — a reader who has
 * narrowed the collection to one court and one year downloads exactly that, not the twenty
 * rows currently on screen.
 *
 * These are ordinary links rather than buttons calling `fetch`. The browser then handles the
 * response's `Content-Disposition` itself, which is what makes the file land in the reader's
 * downloads folder with the name the server chose; a `fetch` would have to rebuild that by
 * hand. The URL is still built in `src/api/client.ts`, so the API base URL is read in one
 * place (`docs/architecture.md` section 6).
 */

import { caseExportUrl } from '@/api/client'
import { BUTTON_SECONDARY } from '@/components/cases/controls'
import { toExportQuery, type CaseFilterState } from '@/utils/caseFilters'
import { formatCount } from '@/utils/dates'

/** Properties of {@link ExportLinks}. */
export interface ExportLinksProps {
  /** The active selection, whose filters the export repeats. */
  readonly filters: CaseFilterState
  /** Number of cases the export will contain, or `null` while it is unknown. */
  readonly total: number | null
}

/**
 * Render the download links.
 *
 * @param props - Component properties.
 * @returns The download group, or `null` when the selection is known to be empty.
 */
export default function ExportLinks({ filters, total }: ExportLinksProps): JSX.Element | null {
  if (total === 0) return null

  const query = toExportQuery(filters)
  const scope =
    total === null ? 'the current selection' : `${formatCount(total)} case${total === 1 ? '' : 's'}`

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-plt-muted text-sm">Download {scope}:</span>
      <a className={BUTTON_SECONDARY} href={caseExportUrl(query, 'csv')} download>
        CSV
        <span className="sr-only"> — download the current selection as a spreadsheet</span>
      </a>
      <a className={BUTTON_SECONDARY} href={caseExportUrl(query, 'json')} download>
        JSON
        <span className="sr-only"> — download the current selection with its full metadata</span>
      </a>
    </div>
  )
}
