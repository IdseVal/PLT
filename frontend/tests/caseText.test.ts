/**
 * Preparation of untrusted court text.
 *
 * The functions under test are the only thing standing between a court's publication system
 * and the page, so what they guarantee is asserted rather than assumed: markup is never
 * interpreted, invisible characters never survive, and the paragraph structure a judgment
 * was published with is the paragraph structure the reader gets.
 */

import { describe, expect, it } from 'vitest'

import { cleanBlockText, cleanInlineText, countWords, toParagraphs } from '@/utils/caseText'

describe('toParagraphs', () => {
  it('splits on blank lines and drops the empties', () => {
    expect(toParagraphs('One.\n\nTwo.\n\n\n   \n\nThree.')).toEqual(['One.', 'Two.', 'Three.'])
  })

  it('falls back to single newlines when a source publishes no blank lines', () => {
    expect(toParagraphs('1. First.\n2. Second.\n3. Third.')).toHaveLength(3)
  })

  it('keeps the line breaks inside a paragraph, which the page renders as written', () => {
    expect(toParagraphs('Considering:\n(a) the first;\n(b) the second.\n\nHeld.')).toEqual([
      'Considering:\n(a) the first;\n(b) the second.',
      'Held.',
    ])
  })

  it('normalises Windows and classic Mac line endings', () => {
    expect(toParagraphs('One.\r\n\r\nTwo.\r\rThree.')).toEqual(['One.', 'Two.', 'Three.'])
  })

  it('returns nothing for an absent or empty text', () => {
    expect(toParagraphs(null)).toEqual([])
    expect(toParagraphs(undefined)).toEqual([])
    expect(toParagraphs('   \n  ')).toEqual([])
  })

  it('treats markup in a judgment as ordinary characters', () => {
    // Nothing here is parsed: the tags come back as text, and it is React that escapes them
    // when they are rendered. See `CaseDetail.test.tsx` for the rendering half of this.
    expect(toParagraphs('<script>alert(1)</script>')).toEqual(['<script>alert(1)</script>'])
  })
})

describe('cleanBlockText', () => {
  it('strips control characters while keeping tabs and newlines', () => {
    const raw = 'Held.' + '\u001b' + '[31m\n\tIndented.'

    expect(cleanBlockText(raw)).toBe('Held.[31m\n\tIndented.')
  })

  it('strips bidirectional overrides, which change how text reads without being visible', () => {
    expect(cleanBlockText('Judgment ' + '\u202e' + 'for the applicant')).toBe('Judgment for the applicant')
  })
})

describe('cleanInlineText', () => {
  it('collapses whitespace so a title fits on one line', () => {
    expect(cleanInlineText('  Stichting   Bollenboos\n v.\tState ')).toBe(
      'Stichting Bollenboos v. State',
    )
  })

  it('is empty for a missing value', () => {
    expect(cleanInlineText(null)).toBe('')
    expect(cleanInlineText(undefined)).toBe('')
  })
})

describe('countWords', () => {
  it('counts whitespace-separated tokens', () => {
    expect(countWords('Het beroep wordt verworpen.')).toBe(4)
    expect(countWords('   ')).toBe(0)
  })
})
