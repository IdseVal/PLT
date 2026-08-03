/**
 * Copy for `/faq`.
 *
 * Each section heading is a question, so the page can be scanned by heading and linked to by
 * anchor. Provisional copy: the licensing and citation answers in particular need the Law
 * group's decision before launch.
 */

import type { StaticPageContent } from '@/types/content'

export const faqPage: StaticPageContent = {
  title: 'Frequently asked questions',
  lead: 'Short answers about what is in the database, how current it is, and how it may be used. The methodology page covers the same ground in more detail.',
  sections: [
    {
      id: 'what-counts',
      heading: 'What counts as a pesticide-related case?',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Public, private and criminal law cases that centre on the effects, governance or liability of pesticide admission, trade or use. That includes challenges to authorisations and their withdrawal, enforcement and penalty decisions, disputes about spraying and drift, product and employer liability claims, and litigation about buffer zones, residues and water quality. Judgments that mention a pesticide only in passing are not included.',
        },
      ],
    },
    {
      id: 'jurisdictions',
      heading: 'Which jurisdictions are covered?',
      blocks: [
        {
          kind: 'paragraph',
          text: 'The tracker starts with the European Union and the Netherlands, and adds member states one at a time. A jurisdiction is only added once two things exist: a connector to that country’s official judicial database, and a keyword list written in the working language of its courts. Jurisdictions that have not yet been onboarded stay visible on the map in a muted state rather than disappearing, so it is always clear what is covered and what is not.',
        },
      ],
    },
    {
      id: 'eu-separate',
      heading: 'Why is the EU shown as its own jurisdiction?',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Because it is one. The Court of Justice and the General Court produce their own body of pesticide case law, on the approval of active substances and on the acts of the Commission and the agencies, which is not the sum of what national courts decide. Counting EU cases separately keeps both figures meaningful.',
        },
      ],
    },
    {
      id: 'updates',
      heading: 'How often is the database updated?',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Weekly. A scheduled run for each jurisdiction asks its source service for everything published or amended since the previous successful run, so a new judgment normally appears within a week of the court publishing it. Each case shows when it was first collected and when it was last checked.',
        },
      ],
    },
    {
      id: 'languages',
      heading: 'Are the judgments available in English?',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Judgments are published here in the language the court issued them in, which is the only authentic version. Where the source service provides other language versions — as EUR-Lex does for EU case law — those are collected too. Searching is intended to work across languages, so that an English-language search can surface a Dutch or French judgment, which is then read in its original language.',
        },
      ],
    },
    {
      id: 'completeness',
      heading: 'Is the collection complete?',
      blocks: [
        {
          kind: 'paragraph',
          text: 'No collection of case law can be. The tracker can only hold what courts publish online, and in most member states the published record is a selection: first-instance judgments in particular are published unevenly. On top of that, cases are selected by matching curated keyword lists, which will occasionally miss a judgment written in unfamiliar vocabulary. The tracker is a finding aid; it is not a substitute for a systematic search of a national database when completeness matters.',
        },
      ],
    },
    {
      id: 'citing',
      heading: 'Can I cite the tracker in my research?',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Yes. Cite the judgment by its own identifier — its ECLI or CELEX number — and cite the tracker as the source through which it was found, with the date of consultation. Because the collection is updated weekly, please record the date: figures and result sets change as new cases arrive.',
        },
      ],
    },
    {
      id: 'download',
      heading: 'Can I download the data?',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Yes. Any selection made with the filters on the all-cases page can be downloaded together with its metadata, for use in a spreadsheet or a statistical package. An API for programmatic access is planned for a later phase of the project.',
        },
      ],
    },
    {
      id: 'reuse',
      heading: 'May I reuse the material?',
      blocks: [
        {
          kind: 'paragraph',
          text: 'The tracker is open access and intended for reuse in academic research and by civil-society organisations, with attribution to Wageningen Law. The judgments themselves are public documents of the courts that issued them, and remain subject to whatever terms those courts attach to their own publications.',
        },
      ],
    },
    {
      id: 'corrections',
      heading: 'I found a case that is missing or wrong. What now?',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Please report it. Corrections are the fastest way the collection improves, and a missing case often points at a gap in a keyword list that is affecting other cases too. Send the case identifier, or a link to it, and a line about what is wrong.',
        },
        {
          kind: 'links',
          items: [
            { label: 'Report a case or a correction', to: '/contact' },
            { label: 'How cases are selected', to: '/methodology' },
          ],
        },
      ],
    },
  ],
  editorialNote:
    'Draft text. The answers on citation, downloading and reuse describe the intended position and need to be confirmed by Wageningen Law — in particular the licence under which the collection and its abstracts are published.',
}
