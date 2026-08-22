/**
 * Content model for the static pages (About, Methodology, FAQ, Contact).
 *
 * Copy is data, not markup: each page is a `StaticPageContent` value in `src/content/`, and
 * `src/components/StaticPage.tsx` renders it. An editor replacing the text therefore never
 * has to touch a component, and every page is laid out and styled identically.
 */

/** A link inside a content block. Use `to` for an application route, `href` for anything else. */
export interface ContentLink {
  /** Visible link text. */
  readonly label: string
  /** Application route, e.g. `/methodology`. Mutually exclusive with `href`. */
  readonly to?: string
  /** Absolute `https:` or `mailto:` target. Mutually exclusive with `to`. */
  readonly href?: string
  /** Optional sentence shown beneath the link. */
  readonly description?: string
}

/** A term and its explanation, rendered as a description list. */
export interface ContentDefinition {
  readonly term: string
  readonly description: string
  /**
   * Names an exclusion mechanism, which renders that mechanism's disclosure under this
   * definition.
   *
   * The binding is this key rather than the definition's wording, so the copy can be
   * rewritten without silently detaching the list of terms from the paragraph describing
   * them.
   */
  readonly mechanism?: ExclusionMechanism
}

/**
 * The exclusion mechanisms `GET /api/exclusions` reports.
 *
 * Three genuinely different claims, which is why they are named separately rather than
 * merged: a term left off a list never runs, a gated term runs but cannot admit a case on
 * its own, and an exclusion pattern rejects a document other terms had already matched.
 */
export type ExclusionMechanism = 'left-off' | 'gated' | 'patterns'

/** One renderable unit of copy. */
export type ContentBlock =
  | { readonly kind: 'paragraph'; readonly text: string }
  | { readonly kind: 'list'; readonly ordered?: boolean; readonly items: readonly string[] }
  | { readonly kind: 'definitions'; readonly items: readonly ContentDefinition[] }
  | { readonly kind: 'links'; readonly items: readonly ContentLink[] }
  /** A short aside, set apart from the running text. */
  | { readonly kind: 'callout'; readonly text: string }
  /**
   * The per-jurisdiction keyword index, read live from `GET /api/filters`.
   *
   * The terms are curated data, not copy: repeating them in a content module would let the
   * page drift from the lists the pipeline actually applies, so this block marks where the
   * index goes and `src/components/KeywordIndex.tsx` fetches and renders it.
   */
  | { readonly kind: 'keyword-index' }

/** A section of a static page, rendered with a level-2 heading. */
export interface ContentSection {
  /** Anchor id, also used as the React key. Lower-case, hyphenated. */
  readonly id: string
  readonly heading: string
  readonly blocks: readonly ContentBlock[]
}

/** A complete static page. */
export interface StaticPageContent {
  /** Level-1 heading, and the document title fragment. */
  readonly title: string
  /** Standfirst paragraph beneath the title. */
  readonly lead: string
  readonly sections: readonly ContentSection[]
  /**
   * Editorial status note rendered at the foot of the page. Every page carries one while the
   * copy is provisional; remove it from the content module once the text is signed off.
   */
  readonly editorialNote?: string
}
