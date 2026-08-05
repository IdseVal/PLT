/**
 * Contact page.
 *
 * The copy lives in `src/content/contact.ts`, including the placeholder contact details that
 * have to be replaced before launch; `StaticPage` renders it.
 */

import StaticPage from '@/components/StaticPage'
import { contactPage } from '@/content/contact'

export default function Contact(): JSX.Element {
  return <StaticPage content={contactPage} />
}
