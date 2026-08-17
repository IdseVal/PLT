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
      id: 'corpus',
      heading: 'The corpus',
      blocks: [
        {
          kind: 'paragraph',
          text: 'The tracker is built like a systematic review, not like a search engine. For each jurisdiction, the publicly available body of case law is first mirrored in full to local storage, and the pesticide-related cases are then selected from that local copy — every document in the mirror is read. Nothing is sampled, and no topical query is ever sent to a source: none of the source services offers a topical filter for pesticides, and the Dutch open-data service offers no full-text search at all, so the only way to be sure nothing is missed is to hold everything and read it.',
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
          text: 'Every document in the corpus is read and judged against the criteria below. The last full run reported “discovered 945,823” for the Netherlands and “discovered 104,143” for the European Union: 1,049,966 documents assessed, with no errors.',
        },
        {
          kind: 'paragraph',
          text: 'The tracker only collects judgments that the courts themselves publish online, and it collects them from the official open-data services rather than from commercial republishers or news reports. Every record keeps a link back to the source publication, and the full text is shown in the language in which the court issued it.',
        },
        {
          kind: 'paragraph',
          text: 'Collection is deliberately unhurried: requests are rate-limited, retried gently when a service is busy, and identify the project and a contact address. These are public research services, paid for out of the public purse, and the tracker is a guest on them.',
        },
      ],
    },
    {
      id: 'inclusion',
      heading: 'Inclusion criteria',
      blocks: [
        {
          kind: 'paragraph',
          text: 'A judgment is included when at least one term from that jurisdiction’s curated keyword list appears in its title, abstract, subject fields or full text. That is the whole criterion. There is no scoring, no weighting and no threshold: a single match suffices, which in turn requires every term on the list to be specific enough to identify a pesticide case on its own.',
        },
        {
          kind: 'callout',
          text: 'An earlier version of the pipeline scored weighted combinations of terms against a threshold. Weighted scoring was removed on 17 August 2026, because letting vague terms accumulate is precisely how false positives are produced. Under the current criterion a term either carries a case alone or it is not on the list.',
        },
        {
          kind: 'paragraph',
          text: 'Applied to the corpus above, this criterion currently includes 3,027 Dutch cases and 1,312 EU cases — 4,339 in total.',
        },
        {
          kind: 'paragraph',
          text: 'The Dutch list holds 862 terms; the EU list holds 555, covering English, French, German and Dutch. About 97% of each list consists of active substances, enumerated from the official registers — the Annex to Commission Implementing Regulation (EU) No 540/2011 for the European Union, the Ctgb register for the Netherlands — and deliberately including substances that are no longer approved, because historic liability litigation is largely about withdrawn substances.',
        },
        {
          kind: 'paragraph',
          text: 'Each jurisdiction has one keyword list, held as a data file in the project repository and versioned alongside the code. The lists are curated by the project’s content manager, a member of Wageningen Law, and not by the developers: deciding which terms identify pesticide litigation in a given legal system is a legal-domain judgement, not a technical one.',
        },
      ],
    },
    {
      id: 'exclusion',
      heading: 'Exclusion criteria',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Precision is protected by three distinct mechanisms. Each is recorded in the repository rather than applied silently, so every exclusion can be audited and argued with.',
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
      ],
    },
    {
      id: 'keyword-index',
      heading: 'The keyword lists, term by term',
      blocks: [
        {
          kind: 'paragraph',
          text: 'Terms do not transfer across borders, so a new jurisdiction cannot be added to the tracker until its list exists. This is a standing precondition of the project, for three reasons:',
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
          text: 'The lists themselves are public. The index below is read live from the same lists the pipeline applies, one disclosure per jurisdiction, grouped by category, with the number of published cases each term currently labels.',
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
        {
          kind: 'paragraph',
          text: 'Keeping the raw source response matters for a database that expects to be reclassified: when the classification scheme is refined, the existing collection can be re-labelled from what is already stored, without going back to the courts’ servers.',
        },
      ],
    },
    {
      id: 'updates',
      heading: 'Update schedule',
      blocks: [
        {
          kind: 'paragraph',
          text: 'A scheduled job runs weekly for each jurisdiction. It resumes from a checkpoint — the newest source modification date the last successful run reached — and asks the source only for what has been published or amended since. A run that fails does not advance the checkpoint, so the next run covers the same ground again rather than leaving a hole in the collection.',
        },
        {
          kind: 'paragraph',
          text: 'Cases are deduplicated on the identifier their own court system gives them — the ECLI in the Netherlands, the CELEX number for EU documents — so a repeated read never creates a second copy. A fingerprint of each document’s content then distinguishes a genuine upstream revision, which updates the existing record in place, from an unchanged re-read, which only refreshes the record’s “last seen” date.',
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
            'It can only hold what courts publish online. In most member states the published record is a selection rather than the whole docket, and first-instance judgments are published unevenly or not at all.',
            'The local mirror is reconciled against the source’s own index rather than assumed to be a perfect copy. The last reconciliation of the Dutch mirror found a contiguous gap of a few thousand identifiers in a single date window (19 February to 10 March 2026), of which only two carried judgment text. That gap is known and not yet repaired. The EU mirror is complete apart from a single document that CELLAR lists but does not serve.',
            'Recall depends on the keyword lists. A pesticide judgment written in unusual vocabulary is missed, and the lists are revised as such gaps are found.',
            'Precision is imperfect. A judgment that merely mentions a pesticide in passing can be included, which is why every record links to its source text and invites the reader to judge for themselves.',
            'Substring matching can attribute a case to a related substance as well as the one it names: a case about alpha-cypermethrin is also labelled cypermethrin.',
            'Coverage begins at different dates in different jurisdictions, depending on how far back the source service’s own archive reaches.',
            'Only two jurisdictions are covered so far. Terms do not transfer across borders, so each new jurisdiction needs its own keyword list before it can be added.',
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
    'Draft text, written from the pipeline as built and the current keyword lists. It is intended to be reviewed and rewritten by Wageningen Law before launch; the figures and the description of the selection criteria should be checked against the implementation whenever a source, a list or a filter stage changes.',
}
