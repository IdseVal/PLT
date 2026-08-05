/**
 * Frequently asked questions.
 *
 * The copy lives in `src/content/faq.ts`; `StaticPage` renders it, one section per question.
 */

import StaticPage from '@/components/StaticPage'
import { faqPage } from '@/content/faq'

export default function Faq(): JSX.Element {
  return <StaticPage content={faqPage} />
}
