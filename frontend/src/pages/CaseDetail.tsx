/**
 * Case detail page: full text in the original language, abstract, classification metadata
 * and a deep link to the source.
 *
 * PLACEHOLDER owned by the shell issue only until the case-detail issue replaces it. That
 * issue builds the page against `GET /api/cases/<jurisdiction>/<source_id>`
 * (`docs/architecture.md` section 5), through `src/api/client.ts`.
 */

import RoutePlaceholder from '@/components/RoutePlaceholder'

export default function CaseDetail(): JSX.Element {
  return (
    <RoutePlaceholder
      title="Case"
      description="A single judgment: its abstract, its classification, the parties and dates, the full text in the language the court issued it in, and a link back to the official publication."
    />
  )
}
