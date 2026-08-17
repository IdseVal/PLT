/**
 * Preparing court text for the page.
 *
 * **Court documents are untrusted input.** They are fetched from public court and EU
 * publication systems, they are not written by this project, and nothing about them is
 * verified before they land in the database. The rule this module exists to keep is simple:
 *
 * > Case text is rendered as text. There is no markup allowlist, because no markup is
 * > allowed at all.
 *
 * Nothing here produces HTML. The functions return plain strings and arrays of strings,
 * which the components render as React children — React escapes them — so the page has no
 * `dangerouslySetInnerHTML` anywhere and no path by which a `<script>`, an `onerror=` or a
 * `javascript:` URL in a judgment could become part of the document. The only source
 * formatting preserved is *whitespace*, and it is preserved by a CSS declaration
 * (`white-space: pre-line`) applied to a text node, which cannot carry behaviour.
 *
 * Control characters are stripped rather than rendered. They are invisible, they are not
 * part of any judgment's meaning, and leaving them in lets a document alter how the text
 * around it appears.
 */

/**
 * Control characters removed from every untrusted string.
 *
 * C0 and C1 ranges plus DEL, keeping the tab and the newline, which carry layout. Written
 * as a character class rather than by hand so the ranges are visible in review.
 */
// eslint-disable-next-line no-control-regex
const CONTROL_CHARACTERS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/g

/**
 * Bidirectional formatting overrides.
 *
 * These reorder the characters around them without appearing on screen, which is how a
 * string can be made to read differently from what it contains. A judgment has no use for
 * them, so they are dropped from every untrusted string.
 */
const BIDI_OVERRIDES = /[\u202a-\u202e\u2066-\u2069]/g

/** Two or more newlines, optionally with blank space between: a paragraph break. */
const PARAGRAPH_BREAK = /\n\s*\n+/

/** Runs of whitespace, collapsed in single-line values such as a title. */
const WHITESPACE_RUN = /\s+/g

/**
 * Clean an untrusted single-line value: a title, a party name, a case number.
 *
 * @param value - Raw value from the API, possibly `null` or `undefined`.
 * @returns The value with control characters and bidirectional overrides removed and
 *   whitespace collapsed, or an empty string when there is nothing to show.
 */
export function cleanInlineText(value: string | null | undefined): string {
  if (value === null || value === undefined) return ''
  return value.replace(CONTROL_CHARACTERS, '').replace(BIDI_OVERRIDES, '').replace(WHITESPACE_RUN, ' ').trim()
}

/**
 * Clean an untrusted block of text, keeping its line structure.
 *
 * @param value - Raw text from the API, possibly `null` or `undefined`.
 * @returns The text with line endings normalised and control characters removed.
 */
export function cleanBlockText(value: string | null | undefined): string {
  if (value === null || value === undefined) return ''
  return value
    .replace(/\r\n?/g, '\n')
    .replace(CONTROL_CHARACTERS, '')
    .replace(BIDI_OVERRIDES, '')
    .trim()
}

/**
 * Split a judgment into the paragraphs the reader sees.
 *
 * Splitting is what makes a long text comfortable to read and cheap to render: the page
 * renders paragraphs, so it can render them progressively instead of committing one
 * multi-megabyte text node to the DOM in a single frame.
 *
 * Sources differ in how they mark a paragraph. Most separate them with a blank line; some
 * extractions arrive with one newline per paragraph and no blank lines at all. Blank lines
 * are preferred when the text has them, and single newlines are used when it does not.
 * Whichever is used, the newlines *inside* a paragraph are kept and rendered with
 * `white-space: pre-line`, so numbered recitals and indented quotations keep their shape.
 *
 * @param text - Raw full text from the API.
 * @returns Cleaned paragraphs, in order, with empty ones dropped. An empty array when there
 *   is no text.
 */
export function toParagraphs(text: string | null | undefined): string[] {
  const cleaned = cleanBlockText(text)
  if (cleaned === '') return []

  const blocks = cleaned.split(PARAGRAPH_BREAK)
  const chosen = blocks.length > 1 ? blocks : cleaned.split('\n')

  return chosen.map((block) => block.trim()).filter((block) => block !== '')
}

/**
 * The name a case is listed and linked under.
 *
 * Not every source publishes a title — Rechtspraak often does not — so the identifier is the
 * fallback and a link is never left without an accessible name.
 *
 * @param title - Title as published by the court, possibly empty or absent.
 * @param sourceId - The case's ECLI or CELEX identifier.
 * @returns A cleaned, non-empty label.
 */
export function caseLabel(title: string | null | undefined, sourceId: string): string {
  return cleanInlineText(title) || cleanInlineText(sourceId) || 'Untitled case'
}

/**
 * Count the words in a block of text, for the "about n words" reading estimate.
 *
 * @param text - Cleaned text.
 * @returns The number of whitespace-separated tokens.
 */
export function countWords(text: string): number {
  const trimmed = text.trim()
  return trimmed === '' ? 0 : trimmed.split(WHITESPACE_RUN).length
}

/**
 * Render a keyword category for a reader.
 *
 * The stored values are the keyword schema's own enum members, written for the file rather
 * than for a person: `active_substance` becomes `Active substance`.
 *
 * @param category - The stored category.
 * @returns The label to show.
 */
export function categoryLabel(category: string): string {
  const spaced = category.replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}
