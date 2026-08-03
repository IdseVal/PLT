/**
 * Copy for `/about`.
 *
 * Provisional copy. The description of the group should be replaced with Wageningen Law's own
 * official wording, and the list of people kept in step with the project team.
 */

import type { StaticPageContent } from '@/types/content'

export const aboutPage: StaticPageContent = {
  title: 'About Wageningen Law',
  lead: 'The Pesticide Litigation Tracker is built and maintained by Wageningen Law, as part of the group’s research on the law of food, agriculture and the environment.',
  sections: [
    {
      id: 'law-group',
      heading: 'The Law group',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Wageningen Law is the chair group for law at Wageningen. Its research sits where law meets the life sciences: food safety and food systems, agriculture, nature and biodiversity, environmental protection and the regulation of technology. The group works on European and national law together, because the questions it studies are almost always governed by both at once.',
        },
        {
          kind: 'paragraph',
          text: 'Pesticide regulation is a case in point. Active substances are approved at European level and products are authorised nationally; enforcement, liability and land-use restrictions are matters for national courts; and the resulting litigation is scattered across dozens of judicial databases in more than twenty languages. The group built this tracker because that scattering makes the field difficult to study.',
        },
      ],
    },
    {
      id: 'why',
      heading: 'Why a litigation tracker',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Courts have become an important arena for pesticide governance. Authorisations are challenged, buffer zones are litigated between growers and their neighbours, exposure and residue claims are brought against manufacturers and employers, and national regulators are taken to task over how they apply European rules. Read together, these judgments show how the law is actually being applied — but only if they can be found and read together.',
        },
        {
          kind: 'paragraph',
          text: 'The tracker is modelled on the Sabin Center for Climate Change Law’s climate litigation databases, which did the same for climate cases and became standard research infrastructure. The intention here is the same: a plain, open, well-documented collection that researchers, litigators, regulators and civil-society organisations can rely on, rather than a commercial product.',
        },
        {
          kind: 'links',
          items: [
            {
              label: 'Sabin Center climate litigation databases',
              href: 'https://climatecasechart.com/',
              description: 'the reference implementation for this project',
            },
          ],
        },
      ],
    },
    {
      id: 'audience',
      heading: 'Who it is for',
      blocks: [
        {
          kind: 'paragraph',
          text: 'The tracker is written for scholars and researchers, legal professionals, non-governmental organisations, civil servants, and anyone else following legal developments in pesticide governance in the European Union and its member states. It is published open access, without registration or payment, so that it is as usable in an NGO with no library budget as in a university.',
        },
      ],
    },
    {
      id: 'team',
      heading: 'The project team',
      blocks: [
        {
          kind: 'paragraph',
          text: 'The Pesticide Litigation Tracker is a project of Wageningen Law, developed in the group’s own research programme. It is run by Edwin Alblas, Idse Val and Vincent Latjes, with the day-to-day work divided between three roles: a content manager, who curates the selection criteria and checks what the pipeline admits; a communication coordinator, who handles contact with users and stakeholders; and system administration for the site itself.',
        },
        {
          kind: 'paragraph',
          text: 'The collection is assembled automatically and reviewed by hand. What the pipeline does, and where its limits lie, is set out in full on the methodology page.',
        },
        {
          kind: 'links',
          items: [
            { label: 'How cases are collected and selected', to: '/methodology' },
            { label: 'Contact the project team', to: '/contact' },
          ],
        },
      ],
    },
  ],
  editorialNote:
    'Draft text. The description of the group should be replaced with Wageningen Law’s own official wording, the list of people confirmed, and a link to the group’s own page added.',
}
