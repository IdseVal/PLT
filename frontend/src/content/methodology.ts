/**
 * Copy for `/methodology`.
 *
 * This page describes what the ingestion pipeline actually does, so it has to stay true to
 * the implementation. Its sources are `docs/core-document.md` sections 2.5 and 2.6, Annex 2a
 * of that document, and `data/keywords/README.md`. If the pipeline changes - a new source,
 * a new filter stage, a different update cadence - this text changes with it.
 *
 * Provisional copy: written to be read and edited by the Law group, not to be published as
 * it stands.
 */

import type { StaticPageContent } from '@/types/content'

export const methodologyPage: StaticPageContent = {
  title: 'Methodology',
  lead: 'How cases reach this database: where they are collected from, how pesticide-related judgments are told apart from the rest of a court’s output, how often the collection is refreshed, and what the method cannot see.',
  sections: [
    {
      id: 'scope',
      heading: 'What counts as a pesticide-related case',
      blocks: [
        {
          kind: 'paragraph',
          text: 'The tracker collects public, private and criminal law cases that centre on the effects, governance and/or liability of pesticide admission, trade and/or use. That definition is deliberately broad. It takes in challenges to the authorisation or withdrawal of a plant protection product, enforcement and penalty decisions, disputes between neighbours and growers over spraying and drift, employer and product liability claims, and public-law litigation about buffer zones, water quality and residue limits.',
        },
        {
          kind: 'paragraph',
          text: 'Cases in which a pesticide is mentioned only in passing — as a detail of a tenancy dispute, say, or as background to an unrelated criminal charge — are outside the scope. Where a judgment sits on the line, the collection errs towards including it and leaving the judgement about relevance to the reader.',
        },
        {
          kind: 'paragraph',
          text: 'Each jurisdiction is a separate entry in one database. The European Union is treated as a jurisdiction in its own right rather than as the sum of its member states, because EU courts produce their own body of pesticide case law and users generally want to read it as such.',
        },
      ],
    },
    {
      id: 'sources',
      heading: 'Sources',
      blocks: [
        {
          kind: 'paragraph',
          text: 'The tracker only collects judgments that the courts themselves publish online, and it collects them from the official open-data services rather than from commercial republishers or news reports. Every record keeps a link back to the source publication, and the full text is shown in the language in which the court issued it.',
        },
        {
          kind: 'definitions',
          items: [
            {
              term: 'European Union — EUR-Lex and the CELLAR repository',
              description:
                'Judgments and opinions of the Court of Justice and the General Court are enumerated through the Publications Office’s public SPARQL endpoint and retrieved through its REST interface, keyed on the CELEX number.',
            },
            {
              term: 'Netherlands — Rechtspraak.nl Open Data',
              description:
                'Published Dutch judgments are enumerated through the open search feed of data.rechtspraak.nl and retrieved as Rechtspraak XML, keyed on the ECLI. Court names, legal areas and procedure types are read from the service’s own controlled vocabularies rather than being maintained by hand.',
            },
          ],
        },
        {
          kind: 'paragraph',
          text: 'Further member states are added one at a time, each with its own connector for that country’s judicial database and its own keyword list. The full survey of national sources is kept in the project’s core document; a jurisdiction appears on the map only once its connector and its keyword list are both in place.',
        },
        {
          kind: 'paragraph',
          text: 'Collection is deliberately unhurried: requests are rate-limited, retried gently when a service is busy, and identify the project and a contact address. These are public research services, paid for out of the public purse, and the tracker is a guest on them.',
        },
      ],
    },
    {
      id: 'selection',
      heading: 'Why selection is keyword-based',
      blocks: [
        {
          kind: 'paragraph',
          text: 'None of the source services offers a reliable topical filter for pesticides. Their subject taxonomies are built around procedural and doctrinal categories — administrative law, competition, environment — and none of them isolates pesticide litigation. The Dutch open-data search offers no full-text search at all. There is therefore no query that can be sent to a court’s database asking for its pesticide cases.',
        },
        {
          kind: 'paragraph',
          text: 'Selection instead happens on the tracker’s side, on language. Candidate judgments are retrieved for a period and matched against a curated list of terms that occur in pesticide litigation in that jurisdiction. A judgment that reaches the threshold is kept, classified and published; one that does not is discarded, and the run records why.',
        },
        {
          kind: 'callout',
          text: 'Keyword matching is only the first stage of the filter chain. The chain is built to take further stages — trained classifiers, filters based on the instruments a judgment cites, a manual review queue — without the source connectors having to be rewritten.',
        },
      ],
    },
    {
      id: 'keyword-lists',
      heading: 'The keyword lists',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Each jurisdiction has one keyword list, held as a data file in the project repository and versioned alongside the code. The lists are curated by the project’s content manager, a member of Wageningen Law, and not by the developers: deciding which terms identify pesticide litigation in a given legal system is a legal-domain judgement, not a technical one.',
        },
        {
          kind: 'paragraph',
          text: 'Terms are weighted rather than treated as equals. An unambiguous term — an active substance, the national plant protection statute — qualifies a judgment on its own. A contextual term — a crop, a cultivation practice, a word such as “drift” or “residents” — counts only in combination with others, and a judgment is kept when its total weight passes a set threshold. Terms that are homonyms in ordinary legal language can be made conditional on another term also matching, which is how the Dutch word “drift” is prevented from pulling in criminal judgments about a fit of anger.',
        },
        {
          kind: 'paragraph',
          text: 'A new jurisdiction cannot be added to the tracker until its list exists, because terms do not transfer across borders. This is a standing precondition of the project, for three reasons:',
        },
        {
          kind: 'list',
          items: [
            'Language. A Dutch list will not find German, French, Polish or Greek judgments. Each list is written in the working language or languages of that jurisdiction’s courts, and multilingual jurisdictions — Belgium, Luxembourg, Malta, Cyprus, Ireland, and the EU itself — need several language sections in one list.',
            'Legal system. Lists carry national statutes, authorising bodies and procedures. The Dutch list names the Ctgb and the Wet gewasbeschermingsmiddelen en biociden; a French list would name ANSES and the Code rural. These have no cross-border equivalent.',
            'Agronomy. The crops and practices that generate pesticide litigation differ by country: bulb and lily cultivation in the Netherlands, viticulture in France, olive groves in Greece.',
          ],
        },
        {
          kind: 'paragraph',
          text: 'Every ingestion run records which terms matched each case, and where in the document they matched. That record is what makes the lists reviewable: the content manager can see which terms are doing the work, which are producing false positives, and which pesticide judgments were only just missed, and can tune the list accordingly. Each change bumps the list version, so it is always possible to say which version of which list admitted a given case.',
        },
      ],
    },
    {
      id: 'updates',
      heading: 'Weekly updates',
      blocks: [
        {
          kind: 'paragraph',
          text: 'A scheduled job runs once a week for each jurisdiction. Rather than re-reading a court’s entire archive, each run picks up from a checkpoint — the point in the publication record the previous successful run reached — and asks only for what has been published or amended since. New pesticide judgments therefore appear within a week of the court publishing them.',
        },
        {
          kind: 'paragraph',
          text: 'A run that fails partway through does not move the checkpoint forward, so the next run covers the same ground again rather than leaving a hole in the collection. Runs can also be started by hand, and can be replayed over a past period, which is how a revised keyword list is tested before it is adopted.',
        },
      ],
    },
    {
      id: 'deduplication',
      heading: 'Deduplication and revisions',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Because the tracker re-reads overlapping periods, it has to be able to recognise a judgment it already holds. Every case is keyed on the identifier its own court system gives it — the ECLI in the Netherlands, the CELEX number for EU documents — which is unique within a jurisdiction. Before anything is stored, the incoming identifier is checked against the database, so a repeated run never creates a second copy of a judgment.',
        },
        {
          kind: 'paragraph',
          text: 'Courts do amend what they publish: a correction, a fuller text, an added summary. The tracker stores a fingerprint of each document’s content and compares it on every encounter. An unchanged document only has its “last seen” date refreshed. A genuinely changed one updates the existing record in place, so the case keeps its address in the tracker and its history is not fragmented across duplicates.',
        },
      ],
    },
    {
      id: 'classification',
      heading: 'What is recorded for each case',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Each published case carries the classification agreed for the project: jurisdiction; law domain (public, private or criminal); law subfield; the litigating parties; the dates of filing and of judgment; and a topic label. Alongside these, the tracker keeps as much of the source metadata as the publishing service exposes — court and instance, procedure type, case numbers, publication and decision dates, language, legal area, citations to instruments and to other cases — together with the untouched source response.',
        },
        {
          kind: 'paragraph',
          text: 'Keeping the raw source response matters for a database that expects to be reclassified: when the topic scheme is refined, the existing collection can be re-labelled from what is already stored, without going back to the courts’ servers.',
        },
      ],
    },
    {
      id: 'limitations',
      heading: 'Limitations',
      blocks: [
        {
          kind: 'paragraph',
          text: 'The tracker is an aid to research, not an authority, and it is worth being explicit about what it cannot do.',
        },
        {
          kind: 'list',
          items: [
            'It can only contain what courts publish online. In most member states the published record is a selection rather than the whole docket, and first-instance judgments are published unevenly or not at all.',
            'Recall depends on the quality of a keyword list. A pesticide judgment written in unusual vocabulary can be missed, and the lists are revised as such gaps are found.',
            'Precision is not perfect either. Judgments that merely mention a pesticide can pass the threshold, which is why every record links to the source text and invites the reader to judge for themselves.',
            'Classification is partly automatic. Topic labels in particular are still being developed and should be treated as a finding aid, not as a settled legal characterisation.',
            'Coverage begins at different dates in different jurisdictions, depending on how far back the source service’s own archive reaches.',
          ],
        },
        {
          kind: 'paragraph',
          text: 'Corrections are welcome and are the fastest way the collection improves. If a case is missing, wrongly included, or wrongly classified, please tell us.',
        },
        {
          kind: 'links',
          items: [
            { label: 'Report a case or a classification error', to: '/contact' },
            { label: 'Frequently asked questions', to: '/faq' },
          ],
        },
      ],
    },
  ],
  editorialNote:
    'Draft text, written from the project’s core document and the pipeline as built. It is intended to be reviewed and rewritten by Wageningen Law before launch; the pipeline description should be checked against the implementation whenever a source or filter stage changes.',
}
