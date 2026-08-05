/**
 * Methodology: how cases are selected, filtered and classified.
 *
 * The copy lives in `src/content/methodology.ts` and must stay true to what the pipeline
 * actually does; `StaticPage` renders it.
 */

import StaticPage from '@/components/StaticPage'
import { methodologyPage } from '@/content/methodology'

export default function Methodology(): JSX.Element {
  return <StaticPage content={methodologyPage} />
}
