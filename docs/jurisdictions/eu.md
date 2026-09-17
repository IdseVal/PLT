# European Union — how cases are collected

| | |
| --- | --- |
| **Jurisdiction code** | `EU` |
| **Courts covered** | Court of Justice of the European Union: the Court of Justice, the General Court and the Civil Service Tribunal |
| **Source** | CELLAR, the Publications Office repository behind EUR-Lex |
| **Legislation list** | `data/legislation/eu.json`, which records its own version |
| **Status** | Corpus rebuilt under the legislation method, «MEASURED: n cases» |
| **Source last checked** | 4 August 2026 |
| **Last reviewed** | 17 September 2026 |

---

## 1. What is covered

The `EU` jurisdiction holds the case law of the Court of Justice of the European Union: the
Court of Justice, the General Court, and the Civil Service Tribunal for the period it
existed. Judgments and orders are collected, and so are the opinions of the Advocates
General. Opinions are not judgments and are stored as opinions, but in pesticide cases they
are often the fullest statement of the legal argument.

**The European Union is a jurisdiction in its own right, never a total of its member
states** (`docs/CORE_DOCUMENT.md` §3.3). The map shows the EU beside the member states rather
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

Selection works the same way in every jurisdiction. Each fetched decision is searched for the
names of the instruments on that jurisdiction's legislation list, and it is selected when any
of them is named in its title, summary, subject labels or text. There is no score and no
threshold, and nothing is marked for review automatically: an instrument either belongs on
the list or it does not (`docs/CORE_DOCUMENT.md` §2.14). The list is not narrowed to improve
precision, because a missed judgment is the expensive error (§2.7).

**The list.** `data/legislation/eu.json` holds 2,246 instruments at version 1.0.0. Nine are
hand-curated, the base instruments of Union pesticide law: plant protection products
(Directive 79/117/EEC, Directive 91/414/EEC, Regulation (EC) No 1107/2009 and the
approved-substance register in Implementing Regulation (EU) No 540/2011), maximum residue
levels (Regulation (EC) No 396/2005), biocides (Directive 98/8/EC and Regulation (EU) No
528/2012), sustainable use (Directive 2009/128/EC) and pesticide statistics (Regulation (EC)
No 1185/2009). The other 2,237 are the acts CELLAR records as based on, amending or
correcting those — above all the implementing regulations that approve, renew and withdraw
active substances — generated from CELLAR rather than typed. REACH, CLP, Aarhus, the general
food law and the Water Framework Directive were considered and left off; the reasons are in
`data/legislation/README.md`.

**Why these instruments.** Two things make an EU list an EU list.

*Legal system.* The Court reviews approvals, refusals, emergency authorisations and access to
the science behind them, and every such case names the instrument it is brought under. An
implementing act is a label of its own: a case about the non-renewal of one substance carries
the regulation that withdrew it, which is what a reader filtering the corpus wants to find.

*Language.* A judgment exists in up to 24 languages and every retrieved version is searched.
The number of a regulation adopted before 2015 is written the same in all of them and is
matched bare, so it is found in any language of the Court. A later act is matched behind its
type word — `Implementing Regulation (EU) 2017/2324`, `règlement d'exécution (UE)
2017/2324`, `Durchführungsverordnung (EU) 2017/2324` — in the four languages the list
carries: English, French, German and Dutch. Titles and short names are carried in the same
four.

**What a test run measured.** «MEASURED: which store or period the legislation list was run
over, how many EU decisions were read and how many were selected». «MEASURED: how many of a
hand-read sample were pesticide or biocide cases, and which instruments selected the ones
that were not». «MEASURED: which cases the keyword method (branch 0.1.0, 1,312 EU cases on
29 August 2026) held that this list does not, and which it adds, on a hand-read». Recall has
not been measured against a reference list of EU pesticide cases.

---

## 4. Documented exceptions

The legislation method has one rule beyond the shared method, and it applies in every
jurisdiction: a year/number instrument number — every directive and decision, and
regulations from 2015 — is never matched bare, because `2009/128` is how Dutch case-law
reporters cite judgments (*NJ 2009/128*), and Dutch is one of this list's languages, and
never as a bare suffixed or bracketed number either, because directives, decisions and, from
2015, regulations share one numbering space: `2003/35/EC` is a Commission decision on
pesticide dossiers and a directive on public participation, and the first run of this list
selected 119 cases on the second. Such an instrument is matched behind its type word instead
— *Decision 2003/35/EC*, *Implementing Regulation (EU) 2017/2324*, *Directive 2009/128* —
which is how the Court writes it. What it costs: a decision citing such an act by its number
alone, or in a language the list has no type words for, would be missed on that citation.

Nothing else is excluded: the list vetoes no document and gates no term. The *metam* rule and
the two weaknesses this section recorded under the keyword method — a word spelled the same
in several languages counted several times, and REACH and ECHA carrying a case between them —
no longer exist, because none of those words is on a legislation list. They are preserved on
branch `0.1.0`.

---

## 5. Known limits

1. **Titles and short names are carried in four of the Court's languages.** In 2024 a
   quarter of decisions had no English text and were stored in the procedural language —
   German, French, Spanish, Italian, Polish, Bulgarian, Greek, Portuguese, Romanian, Dutch,
   Hungarian. A decision in a language the list has no names for matches on the number of
   a pre-2015 regulation, which the Court writes the same way in every language, but not on
   a later act, whose type word the list carries in four languages only, and not on *the
   Plant Protection Products Regulation* written out. This partly heals itself, because a
   case is re-read when a translation is added.
2. **EU cases carry no law domain or subfield** (§2), deliberately, so they are absent from
   any filter built on those fields.
3. **A pesticide case that never names the legislation is missed.** A staff case, a trade
   mark case or a competition case can concern a pesticide producer without citing an
   instrument on the list, and the tracker does not hold it. How many there are: «MEASURED:
   count and character of the cases the 0.1.0 corpus held that the legislation list does
   not select».
4. **Instruments EUR-Lex has not linked are missing until curated.** The generated part of
   the list is what CELLAR records as based on, amending or correcting the base instruments.
   An act CELLAR has not linked that way, or has linked to an instrument outside the base
   set, is not on the list until a curator adds it.
5. **Precision is «MEASURED: share of a hand-read sample that is pesticide or biocide
   litigation».** A wrong case is corrected by taking an instrument off the list, with the
   reason recorded, not by a threshold.
6. **Recall has not been measured** against a reference list of EU pesticide cases. The
   comparison with the keyword corpus counts what changed between two methods, not what
   either missed.
7. **A single query returns at most 10,000 results**, a cap the source introduced in January
   2026. The tracker works around it by reading in date windows, but a lower cap would make
   backfilling materially more expensive.
8. **The corpus was rebuilt under the legislation method on «MEASURED: date of the rebuild»**
   and holds «MEASURED: n cases». The figures in §3 come from that rebuild and from the
   comparison with the corpus of 29 August 2026.
