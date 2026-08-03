/**
 * Class lists shared by the case-law controls.
 *
 * Kept in one module so a form control looks the same wherever it appears, and so the swap
 * to the Wageningen Law styling package stays a change to `tailwind.config.js` plus, at
 * most, this file. Every class here is a Tailwind utility or a theme token from
 * `tailwind.config.js` — never an ad-hoc value (`docs/architecture.md` section 6).
 */

/** Raised surface: filter panel, result card, classification block. */
export const PANEL =
  'border-plt-border bg-plt-panel rounded-sm border'

/** Label above a form control. */
export const LABEL = 'text-plt-ink block text-sm font-medium'

/** Text, date and search inputs. */
export const INPUT =
  'border-plt-border bg-plt-panel text-plt-ink placeholder:text-plt-muted block w-full rounded-sm border px-3 py-2 text-sm'

/** Select controls. Sized like {@link INPUT} so a mixed row lines up. */
export const SELECT =
  'border-plt-border bg-plt-panel text-plt-ink block w-full rounded-sm border px-3 py-2 text-sm'

/** The primary action of a form. */
export const BUTTON_PRIMARY =
  'bg-plt-accent-deep text-plt-inverse rounded-sm px-4 py-2 text-sm font-medium'

/** A secondary action: clear, cancel, a disclosure toggle. */
export const BUTTON_SECONDARY =
  'border-plt-border text-plt-ink hover:border-plt-accent-strong rounded-sm border px-4 py-2 text-sm font-medium'

/** An inline link in body copy, underlined at rest so colour is never the only cue. */
export const LINK =
  'text-plt-accent-strong rounded-sm font-medium underline underline-offset-4'

/** A small, quiet metadata chip, e.g. a topic or a jurisdiction code. */
export const CHIP =
  'bg-plt-accent-soft text-plt-accent-deep border-plt-border inline-flex items-center rounded-sm border px-2 py-0.5 text-xs'
