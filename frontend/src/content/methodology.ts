/**
 * Copy for `/methodology`.
 *
 * This page describes what the ingestion pipeline actually does, structured the way a
 * systematic literature review reports its method: corpus, inclusion criteria, what is
 * recorded, update schedule, limitations. It has to stay true to the implementation. Its
 * sources are `docs/CORE_DOCUMENT.md` sections 2.5, 2.6 and 2.14,
 * `data/legislation/README.md`, and the run logs of the corpus mirror; every figure quoted here
 * comes from one of those, and if the pipeline changes — a new source, a different criterion,
 * a different cadence — this text changes with it.
 *
 * Provisional copy: written to be read and edited by the Law group, not to be published as
 * it stands.
 */

import type { StaticPageContent } from '@/types/content'

export const methodologyPage: StaticPageContent = {
  title: 'Methodology',
  lead: 'How the collection is built: the complete published record of each jurisdiction is mirrored and read in full, a judgment is included when it names at least one pesticide law from a curated legislation list, and the collection is refreshed weekly. This page sets out the corpus, the inclusion criteria, what each included case records, and what the method cannot see.',
  sections: [
    {
      id: 'scope',
      heading: 'What counts as a pesticide-related case',
      blocks: [
        {
          kind: 'paragraph',
          text: 'The tracker collects public, private and criminal law cases that centre on the effects, governance and/or liability of pesticide admission, trade and/or use. That definition is deliberately broad. It takes in challenges to the authorisation or withdrawal of a plant protection product, enforcement and penalty decisions, disputes between neighbours and growers over spraying and drift, employer and product liability claims, and public-law litigation about buffer zones, water quality and residue limits.',
        }
      ],
    },
    {
      id: 'corpus',
      heading: 'The corpus',
      blocks: [
        {
          kind: 'paragraph',
          text: 'To construct the pesticide litigation corpus, the PLT first collects all publicly available case law from every jurisdiction that is included. Next, the PLT screens all available case law for the names of pesticide laws to determine whether something is a pesticide case or not. Which laws are used for each jurisdiction can be seen below. Currently included are the following jurisdictions:',
        },
        {
          kind: 'definitions',
          items: [
            {
              term: 'Netherlands — Rechtspraak.nl Open Data',
              description:
                '945,823 judgments in the local mirror, keyed on the ECLI. Court names, legal areas and procedure types are read from the service’s own controlled vocabularies rather than being maintained by hand.',
            },
            {
              term: 'European Union — EUR-Lex and the CELLAR repository',
              description:
                '104,143 documents of the Court of Justice and the General Court in the local mirror, keyed on the CELEX number, enumerated through the Publications Office’s public SPARQL endpoint and retrieved through its REST interface.',
            },
          ],
        },
        {
          kind: 'paragraph',
          text: 'The tracker only collects judgments that the courts of these jurisdiction have published online, and it collects them from the official open-data services rather than from commercial republishers or news reports. Every record keeps a link back to the source publication, and the full text is shown in the language in which the court issued it. New cases are collected and screened on a weekly basis.',
        }
      ],
    },
    {
      id: 'inclusion',
      heading: 'Inclusion criteria',
      blocks: [
        {
          kind: 'paragraph',
          text: 'A judgment is included when at least one instrument from that jurisdiction’s legislation list is named in its title, abstract, subject fields or full text — by its number, in any of the forms the Official Journal and the courts use, or by its title or customary short name. Applied to the corpus above, this criterion currently includes «NL» Dutch cases and «EU» EU cases — «TOTAL» in total.',
        },
        {
          kind: 'paragraph',
          text: 'Each list carries the base instruments of pesticide law — for the European Union, Regulation (EC) No 1107/2009 and the directives it replaced, the residue regulation, the biocides directive and regulation, the sustainable use directive and the statistics regulation; for the Netherlands, the Wet gewasbeschermingsmiddelen en biociden with its decree and regulation, and the Bestrijdingsmiddelenwet 1962 with the decrees and regulations made under it — together with every implementing, delegated and amending act made under the Union instruments, enumerated from EUR-Lex rather than typed. Instruments that have been repealed are deliberately included, because historic litigation is largely about them.',
        },
        {
          kind: 'paragraph',
          text: 'Each jurisdiction has one legislation list, held as a data file in the project repository and versioned alongside the code. The lists are curated by one of the pesticide law experts associated with the PLT project.',
        },
      ],
    },
    {
      id: 'keyword-index',
      heading: 'The legislation lists, instrument by instrument',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Laws do not transfer across borders, so a new jurisdiction will not be added to the tracker until its list exists. This is a standing precondition of the project, for two reasons:',
        },
        {
          kind: 'list',
          items: [
            'Language. Each list carries the names of its instruments in the working language or languages of that jurisdiction’s courts; the numbers of Union instruments are the same in every language, and multilingual jurisdictions — Belgium, Luxembourg, Malta, Cyprus, Ireland, and the EU itself — need several language sections in one list.',
            'Legal system. Each jurisdiction has its own statutes. The Dutch list names the Wet gewasbeschermingsmiddelen en biociden and the Bestrijdingsmiddelenwet 1962; a French list would name the Code rural et de la pêche maritime. These have no cross-border equivalent.',
          ],
        },
        {
          kind: 'paragraph',
          text: 'As part of good methodological practice, the lists are public. The index below is read live from the same lists the pipeline applies, one disclosure per jurisdiction, grouped by category, with the number of published cases each instrument currently labels.',
        },
        { kind: 'keyword-index' },
      ],
    },
    {
      id: 'recorded',
      heading: 'What each included case records',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Every included case is labelled with the instrument or instruments that selected it, and with each instrument’s category: regulation, directive, decision, implementing or delegated act, national act, decree or ministerial regulation. The labels are public — they are shown on the case page and are filters on the case list — and each label carries the curated citation, so every form in which a court cites an instrument files under one name.',
        },
        {
          kind: 'paragraph',
          text: 'Alongside those labels, each case carries the classification agreed for the project — jurisdiction; law domain (public, private or criminal); law subfield; the litigating parties; the dates of filing and of judgment — and as much of the source metadata as the publishing service exposes: court and instance, procedure type, case numbers, publication and decision dates, language, legal area, and citations to instruments and to other cases, together with the untouched source response.',
        },
      ],
    },
    {
      id: 'updates',
      heading: 'Update schedule',
      blocks: [
        {
          kind: 'paragraph',
          text: 'New cases in the different jurisdictions get published regularly. To stay up to date, the PLT runs a scheduled weekly job to ingest new caselaw into the corpus of each jurisdiction. Next, the methodological pipeline discussed above runs on the new part of the corpus to track new pesticide litigation.',
        },
        {
          kind: 'paragraph',
          text: 'Occasionally, the legislation list of a jurisdiction might change based on new insights, new legislation or other. When this happens, the methodological pipeline will be ran on the entire corpus to come up with the new filtered corpus of the PLT.',
        },
      ],
    },
    {
      id: 'limitations',
      heading: 'Limitations',
      blocks: [
        {
          kind: 'paragraph',
          text: 'The PLT is in essence not more than a tool that presents a corpus resulting from a systematic litigation review. The tracker is meant as an aid to research, and is by no means an authority. In this respect, it is worth being explicit about what it cannot do:',
        },
        {
          kind: 'list',
          items: [
            'The PLT only holds what courts publish online. In most member states the published record is a selection rather than the complete corpus, and first-instance judgments are often published unevenly or not at all. This also means coverage begins at different dates in different jurisdictions, depending on how far back the source service’s own archive reaches.',
            'Filtering is based on the names and numbers of the laws in the legislation lists. A pesticide judgment that never names the legislation — a dispute between neighbours about spraying decided on general tort law, or a criminal case that names only the product — is missed. The lists are revised as such gaps are found.',
            'Precision is imperfect. A judgment that merely cites a pesticide law in passing can be included, which is why every record links to its source text and invites the reader to judge for themselves.',
            'The Union part of each list is compiled from what EUR-Lex records as made under the base instruments. An act EUR-Lex has not linked to them, or a national instrument no court has yet cited by name, is not on the list until a curator adds it.',
            'Only two jurisdictions are covered so far. Laws do not transfer across borders, so each new jurisdiction needs its own legislation list before it can be added.',
          ],
        },
        {
          kind: 'paragraph',
          text: 'Corrections are welcome and are the fastest way the collection improves. If a case is missing, wrongly included, or wrongly classified, please tell us by reaching out:',
        },
        {
          kind: 'links',
          items: [
            { label: 'Report a case or a classification error', to: '/contact' },
          ],
        },
                {
          kind: 'paragraph',
          text: 'Answers to other questions can be found in our FAQ. If your question has not been answered there, feel free to reach out to us as well.',
        },
        {
          kind: 'links',
          items: [
            { label: 'Frequently asked questions', to: '/faq' },
          ],
        },
      ],
    },
  ],
}
