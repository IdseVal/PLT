/**
 * The full text of a judgment, laid out for sustained reading.
 *
 * Core document section 2.1 is explicit that the interface exists first of all to support
 * comfortable reading of case law, and this is the component where that is decided:
 *
 * - **Measure.** The column is capped at the prose measure, roughly 65 characters, rather
 *   than running the width of the window. A 1,400-pixel line loses the reader on every
 *   carriage return.
 * - **Rhythm.** Generous line height and paragraph spacing, and a larger body size from the
 *   `sm` breakpoint up, because these documents are read for an hour at a time.
 * - **Language.** The text carries its own `lang`, so a screen reader pronounces a Dutch
 *   judgment in Dutch (WCAG 3.1.2), and the browser hyphenates it correctly.
 * - **Load.** Paragraphs are revealed across animation frames, so a judgment of several
 *   thousand paragraphs never blocks the main thread in one render. The reveal always
 *   completes, so find-in-page still searches the whole document.
 *
 * **Security.** The text is a court document: untrusted input. It is split into paragraphs
 * and rendered as React text children, which React escapes. There is no
 * `dangerouslySetInnerHTML` here, no HTML parsing and no markup allowlist, because no markup
 * is accepted at all. The only formatting preserved from the source is whitespace, through
 * the CSS `white-space: pre-line`.
 */

import { useMemo, useState } from 'react'

import { SELECT } from '@/components/cases/controls'
import { useProgressiveCount } from '@/hooks/useProgressiveCount'
import { cleanInlineText, countWords, toParagraphs } from '@/utils/caseText'
import { formatCount } from '@/utils/dates'
import type { CaseDocumentRef, CaseDocumentType } from '@/types/api'

/** Properties of {@link CaseFullText}. */
export interface CaseFullTextProps {
  /** Documents attached to the case, in whatever order the API returned them. */
  readonly documents: readonly CaseDocumentRef[]
  /** The language the case itself is recorded in, used to pick the default document. */
  readonly caseLanguage: string | null | undefined
}

/** Paragraphs added per animation frame while the text is revealed. */
const PARAGRAPHS_PER_FRAME = 60

/** Document kinds in the order a reader wants them, most authoritative first. */
const TYPE_ORDER: readonly CaseDocumentType[] = [
  'judgment',
  'opinion',
  'summary',
  'attachment',
  'other',
]

/** Human labels for the document kinds. */
const TYPE_LABELS: Readonly<Record<CaseDocumentType, string>> = {
  judgment: 'Judgment',
  opinion: 'Opinion',
  summary: 'Summary',
  attachment: 'Attachment',
  other: 'Document',
}

/** A BCP 47 language tag, the only thing allowed into a `lang` attribute. */
const LANGUAGE_TAG = /^[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*$/

/**
 * The value to put in a `lang` attribute, if any.
 *
 * The language code comes from the source, so it is validated rather than trusted: an
 * attribute value is one of the few places a string still has meaning to the browser after
 * React has escaped it.
 *
 * @param value - Language code from the API.
 * @returns A valid language tag, or `undefined`.
 */
function toLanguageTag(value: string | null | undefined): string | undefined {
  const cleaned = cleanInlineText(value)
  return LANGUAGE_TAG.test(cleaned) ? cleaned.toLowerCase() : undefined
}

/**
 * The label a document is offered under.
 *
 * @param document - The document.
 * @returns Its kind, with the language when there is one.
 */
function documentLabel(document: CaseDocumentRef): string {
  const kind = TYPE_LABELS[document.doc_type] ?? TYPE_LABELS.other
  const language = cleanInlineText(document.language).toUpperCase()
  return language === '' ? kind : `${kind} (${language})`
}

/**
 * Order the readable documents: most authoritative kind first, own language first.
 *
 * @param documents - Documents from the API.
 * @param caseLanguage - The language recorded on the case.
 * @returns Documents that actually carry text, in reading order.
 */
function readableDocuments(
  documents: readonly CaseDocumentRef[],
  caseLanguage: string | null | undefined,
): CaseDocumentRef[] {
  const language = cleanInlineText(caseLanguage).toLowerCase()

  return documents
    .filter((document) => cleanInlineText(document.full_text) !== '')
    .sort((left, right) => {
      const byType = TYPE_ORDER.indexOf(left.doc_type) - TYPE_ORDER.indexOf(right.doc_type)
      if (byType !== 0) return byType

      const leftOwn = cleanInlineText(left.language).toLowerCase() === language ? 0 : 1
      const rightOwn = cleanInlineText(right.language).toLowerCase() === language ? 0 : 1
      return leftOwn - rightOwn
    })
}

/**
 * Render the full text.
 *
 * @param props - Component properties.
 * @returns The reading column, or a note when the tracker holds no text for the case.
 */
export default function CaseFullText({ documents, caseLanguage }: CaseFullTextProps): JSX.Element {
  const readable = useMemo(
    () => readableDocuments(documents, caseLanguage),
    [documents, caseLanguage],
  )
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const selected = readable.find((document) => document.id === selectedId) ?? readable[0]
  const paragraphs = useMemo(() => toParagraphs(selected?.full_text), [selected])
  const visible = useProgressiveCount(paragraphs.length, PARAGRAPHS_PER_FRAME)

  if (selected === undefined) {
    return (
      <p className="text-plt-muted max-w-prose leading-relaxed">
        The tracker holds no full text for this case. The original publication, linked above,
        remains the authoritative source.
      </p>
    )
  }

  const words = paragraphs.reduce((total, paragraph) => total + countWords(paragraph), 0)

  return (
    <div>
      {readable.length > 1 ? (
        <div className="mb-6 space-y-1">
          <label className="text-plt-muted block text-sm" htmlFor="case-document">
            Document
          </label>
          <select
            id="case-document"
            className={`${SELECT} sm:w-auto`}
            value={selected.id}
            onChange={(event) => {
              setSelectedId(Number.parseInt(event.target.value, 10))
            }}
          >
            {readable.map((document) => (
              <option key={document.id} value={document.id}>
                {documentLabel(document)}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      <p className="text-plt-muted mb-6 text-sm">
        {documentLabel(selected)} &middot; {formatCount(paragraphs.length)}{' '}
        {paragraphs.length === 1 ? 'paragraph' : 'paragraphs'} &middot; about{' '}
        {formatCount(words)} words
      </p>

      <div
        className="text-plt-ink max-w-prose space-y-5 text-base leading-relaxed sm:text-lg sm:leading-loose"
        lang={toLanguageTag(selected.language)}
        aria-busy={visible < paragraphs.length}
      >
        {paragraphs.slice(0, visible).map((paragraph, index) => (
          // The index is a stable key here: the array is derived from one immutable text and
          // is never reordered, only extended as the reveal advances.
          <p key={index} className="whitespace-pre-line">
            {paragraph}
          </p>
        ))}
      </div>
    </div>
  )
}
