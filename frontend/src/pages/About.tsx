/**
 * About Wageningen Law.
 *
 * The copy lives in `src/content/about.ts` so that it can be edited without touching a
 * component; `StaticPage` renders it.
 */

import StaticPage from '@/components/StaticPage'
import { aboutPage } from '@/content/about'

export default function About(): JSX.Element {
  return <StaticPage content={aboutPage} />
}
