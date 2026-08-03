/**
 * All-cases page: full listing with filters, pagination and download.
 *
 * PLACEHOLDER owned by the shell issue only until the all-cases issue replaces it. That
 * issue builds the listing against `GET /api/cases` (`docs/architecture.md` section 5),
 * through `src/api/client.ts`.
 */

import RoutePlaceholder from '@/components/RoutePlaceholder'

export default function AllCases(): JSX.Element {
  return (
    <RoutePlaceholder
      title="All cases"
      description="The complete collection, with filters for jurisdiction, law domain and subfield, court, language, topic and date, and a download of any selection together with its metadata."
    />
  )
}
