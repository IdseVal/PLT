# Jurisdiction methodology documents

Every jurisdiction in the Pesticide Litigation Tracker has a methodology document in this
directory, written **before** its connector. The requirement, and what each document must
record, is set out in `docs/core-document.md` **§2.9** ("Onboarding a jurisdiction"); the
rule that the selection method is identical everywhere and that anything else is an
**explicit documented exception** is **§2.10** ("One method, explicit exceptions").

These documents exist for the reader of the database, not for the developer of the pipeline.
A researcher who finds a case in the PLT — or, more importantly, fails to find one — should
be able to establish from here which courts were searched, by what route, against which
terms, and what the tracker is known not to hold for that jurisdiction.

## What exists

| Code | Jurisdiction | Document | Keyword list | Connector | Endpoints last verified |
| --- | --- | --- | --- | --- | --- |
| `NL` | Netherlands | [`nl.md`](nl.md) | `data/keywords/nl.json` (v1.0.2) | `plt.pipeline.connectors.rechtspraak` | 4 August 2026 |
| `EU` | European Union | [`eu.md`](eu.md) | `data/keywords/eu.json` (v1.0.2) | `plt.pipeline.connectors.eurlex` | 4 August 2026 |

[`TEMPLATE.md`](TEMPLATE.md) is the structure every further jurisdiction fills in. It is
not a suggestion: the sections are the five things §2.9 requires, in the order a reader needs
them, and a jurisdiction document that omits one has not answered the question that section
exists to ask.

The EU is a jurisdiction in its own right, never an aggregate of its member states
(`docs/core-document.md` §3.3). A case decided by the Court of Justice is an `EU` case; a
case decided by a Dutch court applying Regulation (EC) No 1107/2009 is an `NL` case. Neither
document counts the other's cases.

## The three rules these documents follow

1. **Written before the connector.** A connector is a decision about which courts exist and
   which documents are worth fetching. Writing it first turns those decisions into
   undocumented defaults; writing the methodology first makes them arguable.
2. **Every endpoint fact carries the date it was verified against the live service.** Public
   court APIs change without announcement, and three of the facts recorded here were
   discovered only by running against the live service at scale (issues #7 and #8). An
   undated claim about an endpoint is an unfalsifiable one.
3. **Exceptions are presented, not decided.** Keyword lists are curated by the content
   manager (`docs/core-document.md` §2.3 and §2.5), so a jurisdiction document assembles the
   evidence for and the cost of a candidate exception and stops there. Under §2.10 an
   exclusion is a deliberate false negative — the error §2.7 refuses — so it carries a higher
   burden of justification than an inclusion, and that burden is not a developer's to
   discharge.

## Adding a jurisdiction

1. Copy `TEMPLATE.md` to `<code>.md`, lower-case, matching the keyword list file name.
2. Fill in §1 and §2 from primary sources on the court system. This is the part that cannot
   be derived from an API, and it is the part Annex 2 is weakest on: Annex 2 lists apex
   courts almost exclusively, and most pesticide litigation never reaches one.
3. Establish the access route and record every parameter, quirk and limit **with the date you
   verified it**. Add the summary rows to Annex 2a of the core document.
4. Write the keyword list (`data/keywords/README.md`), then run a dry run over a sample
   period and record what it measured in §4 of the document.
5. Record any candidate exception in §5, with its cost, and open a `needs-human` issue for
   the content manager.
