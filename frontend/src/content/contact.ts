/**
 * Copy for `/contact`.
 *
 * The address and the email address below are PLACEHOLDERS. They must be replaced with the
 * project's real contact details before the site is published; the editorial note at the
 * foot of the page says so on the page itself, so a reviewer cannot miss it.
 */

import type { StaticPageContent } from '@/types/content'

export const contactPage: StaticPageContent = {
  title: 'Contact',
  lead: 'Questions about the tracker, corrections to a case, and requests to collaborate all reach the same team. We read everything, and corrections are acted on first.',
  sections: [
    {
      id: 'general',
      heading: 'General enquiries',
      blocks: [
        {
          kind: 'paragraph',
          text: 'For questions about the project, about how to use the database, or about the research behind it, write to the project team by email.',
        },
        {
          kind: 'links',
          items: [
            {
              label: 'plt@wur.nl',
              href: 'mailto:plt@wur.nl',
              description: 'placeholder address, to be confirmed before launch',
            },
          ],
        },
      ],
    },
    {
      id: 'corrections',
      heading: 'Reporting a missing, wrong or misclassified case',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Corrections are the most useful thing you can send us. A case that the pipeline missed usually means a term is absent from a keyword list, which is affecting other cases too, so a single report often improves the collection well beyond the case reported.',
        },
        {
          kind: 'paragraph',
          text: 'To let us act quickly, please include:',
        },
        {
          kind: 'list',
          ordered: true,
          items: [
            'the case identifier — the ECLI for a national judgment, the CELEX number for an EU document — or a link to the judgment on the court’s own website;',
            'what is wrong: the case is absent, it does not belong in the collection, or a classification label (jurisdiction, law domain, subfield, parties, dates, topic) is incorrect;',
            'for a missing case, the terms in the judgment that mark it as pesticide-related, if you have them to hand.',
          ],
        },
      ],
    },
    {
      id: 'collaboration',
      heading: 'Research collaboration and new jurisdictions',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Adding a member state depends on two things: access to that country’s published case law, and a keyword list in the working language of its courts, covering the national statutes, the authorising body, and the crops and practices that generate local litigation. That second part is legal-domain work, and it is where collaboration helps most.',
        },
        {
          kind: 'paragraph',
          text: 'If you work on pesticide litigation in a jurisdiction that is not yet covered and would be willing to help build or review its list, we would be glad to hear from you. The same goes for researchers who want to use the collection in a study, or who would like a data extract prepared.',
        },
      ],
    },
    {
      id: 'press',
      heading: 'Press and media',
      blocks: [
        {
          kind: 'paragraph',
          text: 'For media enquiries, please write to the project team by email and mention your deadline, and you will be put in touch with the appropriate member of Wageningen Law.',
        },
      ],
    },
    {
      id: 'post',
      heading: 'Postal address',
      blocks: [
        {
          kind: 'definitions',
          items: [
            {
              term: 'Wageningen Law (the Law group)',
              description:
                'Leeuwenborch, Hollandseweg 1, 6706 KN Wageningen, The Netherlands (placeholder — to be confirmed)',
            },
          ],
        },
        {
          kind: 'paragraph',
          text: 'Post is slower than email and is not the right route for reporting a case.',
        },
      ],
    },
  ],
  editorialNote:
    'Draft text. The email address and the postal address on this page are placeholders and must be replaced with the project’s real contact details before launch. If a contact form is preferred over a published address, this page is where it goes.',
}
