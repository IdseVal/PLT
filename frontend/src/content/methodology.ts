/**
 * Copy for `/methodology`.
 *
 * This page describes what the ingestion pipeline actually does, structured the way a
 * systematic literature review reports its method: corpus, inclusion criteria, exclusion
 * criteria, what is recorded, update schedule, limitations. It has to stay true to the
 * implementation. Its sources are `docs/core-document.md` sections 2.5 and 2.6,
 * `data/keywords/README.md`, and the run logs of the corpus mirror; every figure quoted here
 * comes from one of those, and if the pipeline changes — a new source, a different criterion,
 * a different cadence — this text changes with it.
 *
 * Provisional copy: written to be read and edited by the Law group, not to be published as
 * it stands.
 */

import type { StaticPageContent } from '@/types/content'

export const methodologyPage: StaticPageContent = {
  title: 'Methodology',
  lead: 'How the collection is built: the complete published record of each jurisdiction is mirrored and read in full, a judgment is included when it carries at least one term from a curated keyword list, and the collection is refreshed weekly. This page sets out the corpus, the inclusion and exclusion criteria, what each included case records, and what the method cannot see.',
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
          text: 'To construct the pesticide litigation corpus, the PLT first collects all publicly available case law from every jurisdiction that is included. Next, the PLT screens all available caselaw on simple keyword filters to determine whether something is a pesticide case or not. What keywords are used for each jurisdiction can be seen below. Currently included are the following jurisdictions:',
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
          text: 'A judgment is included when at least one term from that jurisdiction’s curated keyword list appears in its title, abstract, subject fields or full text. Applied to the corpus above, this criterion currently includes 3,027 Dutch cases and 1,312 EU cases — 4,339 in total.',
        },
        {
          kind: 'paragraph',
          text: 'About 97% of each keyword list consists of active substances, enumerated from the official registers — the Annex to Commission Implementing Regulation (EU) No 540/2011 for the European Union, the Ctgb register for the Netherlands — and deliberately including substances that are no longer approved, because historic liability litigation is largely about withdrawn substances. Other keywords relate to names of regulations, pesticide authorities and specific agricultural practices.',
        },
        {
          kind: 'paragraph',
          text: 'Each jurisdiction has one keyword list, held as a data file in the project repository and versioned alongside the code. The lists are curated by one of the pesticide law experts associated with the PLT project.',
        },
      ],
    },
    {
      id: 'keyword-index',
      heading: 'The keyword lists, term by term',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Terms do not transfer across borders, so a new jurisdiction will not be added to the tracker until its list exists. This is a standing precondition of the project, for three reasons:',
        },
        {
          kind: 'list',
          items: [
            'Language. A Dutch list will not find German, French, Polish or Greek judgments. Each list is written in the working language or languages of that jurisdiction’s courts, and multilingual jurisdictions — Belgium, Luxembourg, Malta, Cyprus, Ireland, and the EU itself — need several language sections in one list.',
            'Legal system. Lists carry national statutes, authorising bodies and procedures. The Dutch list names the Ctgb and the Wet gewasbeschermingsmiddelen en biociden; a French list would name ANSES and the Code rural. These have no cross-border equivalent translation.',
            'Agronomy. The crops and practices that generate pesticide litigation differ by country: bulb and lily cultivation in the Netherlands, viticulture in France, olive groves in Greece. The lists are populated specifically for each jurisdiction in collaboration with a pesticide law expert from that jurisdiction.',
          ],
        },
        {
          kind: 'paragraph',
          text: 'As part of good methodological practice, the lists are public. The index below is read live from the same lists the pipeline applies, one disclosure per jurisdiction, grouped by category, with the number of published cases each term currently labels.',
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
          text: 'Every included case is labelled with the term or terms that selected it, and with each term’s category: active substance, product class, statute, authority, procedure, crop or practice, exposure or harm, environment and brand, among others. The labels are public — they are shown on the case page and are filters on the case list — and each label carries the curated spelling, so every spelling of a substance files under one name.',
        },
        {
          kind: 'paragraph',
          text: 'Alongside those labels, each case carries the classification agreed for the project — jurisdiction; law domain (public, private or criminal); law subfield; the litigating parties; the dates of filing and of judgment — and as much of the source metadata as the publishing service exposes: court and instance, procedure type, case numbers, publication and decision dates, language, legal area, and citations to instruments and to other cases, together with the untouched source response.',
        },
      ],
    },
    {
      id: 'exclusion',
      heading: 'Exclusion criteria',
      blocks: [
        {
          kind: 'paragraph',
          text: 'The approach using extensive lists of keywords will return some false positives. To make the PLT more useful, another exclusion filter must be applied. Precision in this sense is protected by three distinct mechanisms:',
        },
        {
          kind: 'definitions',
          items: [
            {
              term: 'Terms deliberately left off the lists',
              description:
                'A candidate term that would select cases outside the scope is removed, and the removal and its reason are recorded (in data/keywords/excluded_nl.json and excluded_eu.json). Examples: “werkzame stof” (active substance — a phrase every chemical, pharmaceutical and food case uses), “omwonenden” (neighbours — every planning case), “bufferzone”, “blootstelling” (exposure — asbestos and noise cases), “residu”, crop names such as “lelieteelt” and “boomkwekerij”, the authorities NVWA, EFSA and ECHA, and the Wet op de economische delicten, which alone had pulled in 577 unrelated cases. The Dutch term “water” was removed on the same reasoning: a gate governs whether a term may include a case, not what the case is then said to be about, and as a public label it had described 307 pesticide judgments as being about the substance water.',
            },
            {
              term: 'Gated terms',
              description:
                'An active substance whose official name is also an ordinary word — beer, talc, vinegar — stays on the list but is gated: it cannot include a case on its own, and counts only when a plant-protection term is also present in the same document.',
            },
            {
              term: 'Exclusion patterns',
              description:
                'A small number of known phrase traps veto a document outright, however many terms it matched. The clearest example is the standard Dutch toxicology-report sentence “geen aanwijzingen voor de aanwezigheid van geneesmiddelen, drugs en/of bestrijdingsmiddelen”, which names pesticides in order to rule them out and is boilerplate in homicide judgments.',
            },
          ],
        },
        { kind: 'exclusion-index' },
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
          text: 'Occasionally, the keyword list of a jurisdiction might change based on new insights, new pesticides or other. When this happens, the methodological pipeline will be ran on the entire corpus to come up with the new filtered corpus of the PLT.',
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
            'Filtering is based on text-strings in the keyword lists. A pesticide judgment written in unusual vocabulary, or containing misspellings of keyword terms, is missed. The lists are revised as such gaps are found.',
            'Precision is imperfect. A judgment that merely mentions a pesticide in passing can be included, which is why every record links to its source text and invites the reader to judge for themselves.',
            'Substring matching can attribute a case to a related substance as well as the one it names: a case about alpha-cypermethrin is also labelled cypermethrin.',
            'Only two jurisdictions are covered so far. Terms do not transfer across borders, so each new jurisdiction needs its own keyword list before it can be added.',
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
