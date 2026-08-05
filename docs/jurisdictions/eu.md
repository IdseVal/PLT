# European Union — how cases are collected

| | |
| --- | --- |
| **Jurisdiction code** | `EU` |
| **Courts covered** | Court of Justice of the European Union: the Court of Justice, the General Court and the Civil Service Tribunal |
| **Source** | CELLAR, the Publications Office repository behind EUR-Lex |
| **Keyword list** | `data/keywords/eu.json`, version 1.1.0 |
| **Status** | Connector built and tested against the live service; no cases stored yet |
| **Source last checked** | 4 August 2026 |
| **Last reviewed** | 5 August 2026 |

---

## 1. What is covered

The `EU` jurisdiction holds the case law of the Court of Justice of the European Union: the
Court of Justice, the General Court, and the Civil Service Tribunal for the period it
existed. Judgments and orders are collected, and so are the opinions of the Advocates
General. Opinions are not judgments and are stored as opinions, but in pesticide cases they
are often the fullest statement of the legal argument.

**The European Union is a jurisdiction in its own right, never a total of its member
states** (`docs/core-document.md` §3.3). The map shows the EU beside the member states rather
than colouring them, and an EU case is counted once, as an EU case.

A case belongs to the jurisdiction of the court that decided it. A Court of Justice ruling on
a preliminary reference from a Dutch court is an `EU` case; the Dutch court's own judgment,
before and after the reference, is an `NL` case ([`nl.md`](nl.md)). A search for pesticide
cases in the Netherlands will not return *Blaise*. The unit of selection is the **CELEX
number**: one CELEX is one case, however many language versions stand behind it.

**How pesticide litigation reaches these courts.** Four routes, all of them present in the
tracker:

- **Annulment actions before the General Court**, brought by NGOs or by producers against
  Commission approval decisions — PAN Europe `T-536/22`, and the biocides cases Troy Chemical
  `T-297/21`, Dakem `T-341/23` and SBM Développement `T-667/22`.
- **Preliminary references to the Court of Justice** on the interpretation of Regulation (EC)
  No 1107/2009 and its predecessors — *Blaise* `C-616/17`, on glyphosate, is the standard
  example.
- **Appeals from the General Court** — PAN Europe `C-308/22` and `C-309/22` on emergency
  authorisations of neonicotinoid-treated seeds, and Commission v Pollinis France on the bee
  guidance documents.
- **Access-to-documents and Aarhus litigation** about the evidence behind pesticide
  approvals.

**Not covered.** National courts applying EU pesticide law, which belong to their own
jurisdictions; the Commission, EFSA and the Board of Appeal of ECHA, which are administrative
bodies rather than courts and appear here only as the subject of litigation; regulations,
directives and implementing acts, which are legislation rather than case law and appear only
as citations from cases; and the editorial summaries and Official Journal notices that
accompany judgments, which would otherwise return the same case several times over.

---

## 2. Where the data comes from

Cases are read from **CELLAR**, the Publications Office repository that supplies EUR-Lex,
through its query and retrieval interfaces. Nothing is taken from the CJEU's own website.
CELLAR holds **104,087 distinct case-law CELEX numbers**, which is the population the tracker
works from.

A year is roughly a thousand and a half decisions. Over 2024 the tracker retrieved 1,548:
501 Court of Justice judgments, 493 General Court judgments, 217 Advocate General opinions
and 337 orders.

**Languages.** A judgment exists in up to 24 languages, and these are versions of one case
rather than separate cases. The tracker takes English where it is available, the procedural
language otherwise, and any language CELLAR holds if neither can be served. Every version
retrieved is stored against the same case and all of them are searched. The fallback matters:
without it a fifth of decisions came out with no text at all, because an English version was
missing while another language was there for the asking; with it, roughly one in a hundred
has no retrievable text.

A small number of decisions exist only as a metadata notice, in any language. The notice is
stored and the case is kept.

Everything CELLAR exposes is kept, including the citation relations in the form CELLAR states
them — *cites*, *interprets*, *declares valid*, *declares void*, *applies* — rather than
flattened to a single kind of link. *Blaise* carries 24 citations, *Bayer* 91.

**What the source does not offer.** CELLAR has no field for the parties, so they are read out
of the case title, which the CJEU structures predictably. Its subject classification is a
policy tree — "Internal policy of the European Union → Chemicals → Plant protection
products" — and not the public, private or criminal division the tracker classifies cases by,
so EU cases carry no law domain rather than a guessed one. In a research database a wrong
classification is worse than a missing one.

The tracker re-reads CELLAR weekly by modification date, which picks up newly published and
newly corrected decisions alike. Historical periods are backfilled by decision date instead.

---

## 3. How cases are selected

Selection works the same way in every jurisdiction. Each fetched decision is scored against
that jurisdiction's keyword list; a decision reaching the threshold is selected, and one
scoring just above it is additionally marked for a content manager to confirm or reject. The
threshold is not raised to improve precision, because a missed judgment is the expensive
error (`docs/core-document.md` §2.7).

**The list.** `data/keywords/eu.json` holds 65 terms in four languages — 41 English, 9
French, 9 German, 6 Dutch — of which 45 qualify a decision on their own, 12 are strong and 8
are contextual. A decision is selected at a score of 3 or more, and one scoring below 6 is
marked for review. Terms in the title, the summary or CELLAR's subject labels count for more
than terms in the body, because those fields are curated topical vocabulary here.

**Why these terms.** Three things make an EU list an EU list.

*Language.* This is where the EU differs from every member state. The list carries English
plus the three most common procedural languages for pesticide cases, and a term matching in
any retrieved version qualifies the case.

*Legal system.* Instrument numbers are the sharpest signals available and the only ones that
work in every language: 1107/2009 (plant protection products), 528/2012 (biocides), 396/2005
(maximum residue levels), 2009/128 (sustainable use), 91/414 (the predecessor directive).
Beside them sit the procedural vocabulary of EU pesticide law — approval and non-renewal of
active substances, zonal authorisation and mutual recognition, the Article 53 emergency
derogation, SCoPAFF, the rapporteur Member State — and, as contextual terms, the wider
chemicals regime: REACH, CLP, ECHA, EFSA and Aarhus.

*Agronomy.* Largely absent, and rightly. The EU courts do not try spray-drift disputes
between neighbours; they review approvals, refusals and access to the science behind them.
The list carries the science instead: endocrine-disrupting properties, bee health and
pollinators, operator and bystander exposure, seed treatment, integrated pest management.

**What a test run measured.** Over the whole of 2024 the tracker read 1,548 decisions and 54
passed the filter. All 54 were read by hand, and about 17 were genuine pesticide or biocide
cases — a precision of roughly one in three.

The ranking is the reassuring part. The cases a pesticide lawyer would name first come out
well above everything else: PAN Europe `C-308/22` and `C-309/22` at 58.5 and 60.5, PAN Europe
`T-536/22` at 53.5, Commission v Pollinis France at 20.0, the biocides cases between 18 and
19.5. A smaller check makes the same point: on 1 October 2019 the Court delivered eight
decisions, and the filter passed exactly one — *Blaise*, on glyphosate, at 47.5 — with the
other seven scoring zero.

The scores separate the two groups sharply. Of the 54, 18 scored 12 or more and were almost
all genuine; 21 scored between 3.0 and 3.9 and almost none were. That bottom band is 40% of
everything selected and holds nearly all the wrong cases, which is why it is reviewed rather
than cut off. Recall has not been measured against a reference list, and the ranking is not a
substitute for one.

---

## 4. Documented exceptions

None. The EU list adds nothing to the shared method: it vetoes no document and gates no term.

Two weaknesses in the list are known and are being weighed by the content manager, who owns
curation (`docs/core-document.md` §2.3):

- **A word spelled the same way in several languages is counted several times.** *Pesticide*
  is both English and French, so one occurrence scores three times the threshold. Nine
  literals in the list are shared this way. It distorts the ranking rather than the verdict,
  since each of those terms would qualify a case on its own, but a case that scores 9 on a
  single word lands in the confident band and escapes review.
- **ECHA and REACH can carry a case over the threshold between them.** Twelve of the 54
  selected in 2024 arrived this way, including an appeal about harmonised standards, an
  oxo-degradable plastics case and one on lead in ammunition. Requiring a pesticide-specific
  term alongside them would remove those, but the access-to-documents and Aarhus cases that
  are genuine often discuss the chemicals regime at length, and it has not been measured
  whether they always name a pesticide as well. Until that is known, the cost of the rule is
  unknown, and an unknown cost in recall is not one this project accepts.

---

## 5. Known limits

1. **Only four of the Court's languages are covered by the list.** In 2024 a quarter of
   decisions had no English text and were stored in the procedural language — German, French,
   Spanish, Italian, Polish, Bulgarian, Greek, Portuguese, Romanian, Dutch, Hungarian. For
   the languages without a section, only the instrument numbers can match. This partly heals
   itself, because a case is re-read when a translation is added, but it is a real gap today.
2. **EU cases carry no law domain or subfield** (§2), deliberately, so they are absent from
   any filter built on those fields.
3. **Precision is about one case in three**, with the wrong cases concentrated just above the
   threshold and marked for review rather than rejected.
4. **Recall has not been measured** against a reference list of EU pesticide cases.
5. **Trade mark judgments quote the Nice Classification**, whose class 5 reads "Fungicides,
   herbicides", so EUIPO cases can score on a product-class term without concerning
   pesticides. EUIPO cases are a large share of the General Court's docket, so this grows
   with the corpus.
6. **A single query returns at most 10,000 results**, a cap the source introduced in January
   2026. The tracker works around it by reading in date windows, but a lower cap would make
   backfilling materially more expensive.
7. **No cases have been stored yet.** Every figure here comes from test runs against the live
   service.
