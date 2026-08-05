/**
 * What the accessibility harness checks: every route, at every width, in every data state
 * that route can actually be in.
 *
 * ## Where this list comes from
 *
 * It is the union of the headless-Chrome passes that were run by hand on #12 (6 widths ×
 * 3 states), #13 (5 widths × 2 states) and the shell (8 routes × 3 widths), which found a
 * WCAG 1.4.1 contrast failure on a footer link and a focus ring that silently never applied
 * to Chrome's date inputs. Those runs are not repeatable and guarded nothing after the day
 * they were performed; this file is them, written down.
 *
 * ## Widths
 *
 * Six, from the narrowest viewport the site claims to support to a wide desktop, chosen at
 * and around the Tailwind breakpoints the layout actually switches on rather than at round
 * numbers:
 *
 * | Width | Why |
 * | --- | --- |
 * | 320 | The narrowest phone still in use. Below `sm`; everything is one column. |
 * | 414 | A large phone. Still below `sm`, but a third more room, which is where a two-item flex row starts to fit and then not quite fit. |
 * | 768 | Exactly `md`. A boundary is worth sampling *at* the value, because `min-width` is inclusive and an off-by-one there is invisible in review. |
 * | 1024 | Exactly `lg`, where the home sidebar moves beside the map and the All-cases filter column appears. |
 * | 1280 | `xl`, a laptop. |
 * | 1440 | A desktop monitor, where a `max-w` cap either holds the measure or lets a line run to 200 characters. |
 *
 * ## States
 *
 * `populated`, `empty` and `error` (`fixtures.mjs`). Static pages are listed once, not three
 * times: they make no API call, so the three states would render the same bytes and cost
 * three times the wall clock to prove it. The states are attached per route rather than
 * multiplied across all of them for that reason.
 *
 * The zero-data state is not an edge case here. At launch only NL and EU hold cases, so most
 * of the map is the muted "no cases yet" treatment and `/cases` can legitimately be empty.
 */

import { CASE_SOURCE_ID, MISSING_SOURCE_ID, VALID_TOKEN } from './fixtures.mjs'

/** Viewport widths, narrowest first. */
export const WIDTHS = [320, 414, 768, 1024, 1280, 1440]

/**
 * Viewport height. Fixed, and tall enough that a page is not scrolled by default: the
 * vertical scrollbar's width is what a horizontal-overflow check is most easily fooled by,
 * so it is held constant instead of varying with the page.
 */
export const HEIGHT = 900

/**
 * axe rulesets. The five the earlier runs used, unchanged.
 *
 * `best-practice` is not a WCAG conformance requirement but was part of every run to date
 * and catches the structural mistakes — a skipped heading level, a landmark outside a
 * region — that turn a conformant page into an unreadable one.
 */
export const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']

/**
 * @typedef {object} PageCase
 * @property {string} name - Short label used in the report.
 * @property {string} path - Path to visit, percent-encoded as a browser would send it.
 * @property {import('./fixtures.mjs').DataState} state - Which stub server to visit it on.
 * @property {string} [waitFor] - Optional CSS selector that must appear before checks run.
 */

/** Percent-encoded so the path is what the browser puts on the wire; an ECLI is full of colons. */
const CASE_PATH = `/cases/NL/${encodeURIComponent(CASE_SOURCE_ID)}`
const MISSING_CASE_PATH = `/cases/NL/${encodeURIComponent(MISSING_SOURCE_ID)}`

/**
 * Every page the harness loads.
 *
 * @type {readonly PageCase[]}
 */
export const PAGE_CASES = [
  // The home page: title, search bar, map and the twenty-case sidebar.
  { name: 'home', path: '/', state: 'populated' },
  { name: 'home', path: '/', state: 'empty' },
  { name: 'home', path: '/', state: 'error' },

  // The listing, with the filter column, the paginator and the export links.
  { name: 'cases', path: '/cases', state: 'populated' },
  { name: 'cases', path: '/cases', state: 'empty' },
  { name: 'cases', path: '/cases', state: 'error' },

  // A filtered listing: the filter controls carry values, which is when a select's chosen
  // option and the date inputs are on screen rather than at their defaults.
  {
    name: 'cases?filtered',
    path: '/cases?q=glyphosate&jurisdiction=NL&law_domain=public&date_from=2020-01-01&sort=relevance&page=2',
    state: 'populated',
  },

  // One case. `populated` is the full record; `empty` is a metadata-only ECLI, which is a
  // real record and the state in which the page has the least to lay out.
  { name: 'case', path: CASE_PATH, state: 'populated' },
  { name: 'case', path: CASE_PATH, state: 'empty' },
  { name: 'case', path: CASE_PATH, state: 'error' },
  { name: 'case?missing', path: MISSING_CASE_PATH, state: 'populated' },

  // The four static routes. One state each: they make no API call.
  { name: 'about', path: '/about', state: 'populated' },
  { name: 'methodology', path: '/methodology', state: 'populated' },
  { name: 'faq', path: '/faq', state: 'populated' },
  { name: 'contact', path: '/contact', state: 'populated' },

  // The two mailing-list routes, in each outcome a reader can arrive at.
  { name: 'confirm?valid', path: `/subscribe/confirm?token=${VALID_TOKEN}`, state: 'populated' },
  { name: 'confirm?expired', path: '/subscribe/confirm?token=expired-link', state: 'populated' },
  { name: 'confirm?no-token', path: '/subscribe/confirm', state: 'populated' },
  { name: 'confirm?api-down', path: `/subscribe/confirm?token=${VALID_TOKEN}`, state: 'error' },
  { name: 'unsubscribe?valid', path: `/unsubscribe?token=${VALID_TOKEN}`, state: 'populated' },
  { name: 'unsubscribe?expired', path: '/unsubscribe?token=expired-link', state: 'populated' },
  { name: 'unsubscribe?form', path: '/unsubscribe', state: 'populated' },

  // The catch-all route.
  { name: 'not-found', path: '/no-such-page', state: 'populated' },
]
