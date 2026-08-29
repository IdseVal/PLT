/**
 * The site menu, defined once and consumed by both the header and the footer.
 *
 * These four items, their wording and their order are fixed by the design brief
 * (`docs/CORE_DOCUMENT.md` section 3.3) and repeated in `docs/architecture.md` section 6 and
 * in the README. They are not an editorial choice: do not rename, reorder or extend this
 * list without a change to those documents.
 */

/** One entry in the site menu. */
export interface MenuItem {
  /** Visible label. Fixed by the brief. */
  readonly label: string
  /** Application route the item points at. */
  readonly to: string
}

/** The header menu, in brief order. */
export const SITE_MENU: readonly MenuItem[] = [
  { label: 'About Wageningen Law', to: '/about' },
  { label: 'Methodology', to: '/methodology' },
  { label: 'FAQ', to: '/faq' },
  { label: 'Contact', to: '/contact' },
] as const

/** Database links repeated in the footer, alongside the site menu. */
export const FOOTER_DATABASE_LINKS: readonly MenuItem[] = [
  { label: 'Home', to: '/' },
  { label: 'All cases', to: '/cases' },
] as const
