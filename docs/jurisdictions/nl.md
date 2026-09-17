# Netherlands — how cases are collected

| | |
| --- | --- |
| **Jurisdiction code** | `NL` |
| **Courts covered** | Every court publishing through the Raad voor de rechtspraak, at all instances |
| **Source** | Rechtspraak open data portal, `data.rechtspraak.nl` |
| **Legislation list** | `data/legislation/nl.json`, which records its own version |
| **Status** | Corpus rebuilt under the legislation method, «MEASURED: n cases» |
| **Source last checked** | 4 August 2026 |
| **Last reviewed** | 17 September 2026 |

---

## 1. What is covered

The `NL` jurisdiction holds decisions of Dutch courts as published by the Raad voor de
rechtspraak. The portal covers the whole judiciary, and so does the tracker: the rechtbanken
and gerechtshoven, the Centrale Raad van Beroep, the College van Beroep voor het
bedrijfsleven (CBb), the Afdeling bestuursrechtspraak van de Raad van State and the Hoge
Raad, together with the conclusies of the Parket bij de Hoge Raad and the disciplinary
tribunals. The portal's own list of 261 courts is what the tracker works from, so a court is
covered because the source publishes it, not because it appears on any list kept here.

The courts of the Caribbean parts of the Kingdom — Aruba, Curaçao, Sint Maarten and the BES
islands — publish through the same portal and are part of the `NL` jurisdiction. EU pesticide
law does not apply in those territories, so their judgments turn on local legislation rather
than on Regulation (EC) No 1107/2009. The portal's court type is stored with each court, so
Caribbean cases can be identified as such.

A case belongs to the jurisdiction of the court that decided it. A Dutch court applying
Regulation (EC) No 1107/2009 gives an `NL` case; a Court of Justice ruling on a preliminary
reference from a Dutch court gives an `EU` case ([`eu.md`](eu.md)). Neither jurisdiction
counts the other's cases. The unit of selection is the **ECLI**, which is also what
identifies a case uniquely and keeps it from being stored twice.

**Where Dutch pesticide litigation is heard.** Four families of case account for most of it,
and they sit at different points in the hierarchy:

- **Authorisation and its withdrawal.** Challenges to decisions of the Ctgb, the board that
  authorises plant protection products and biocides, heard by the CBb — the withdrawal of the
  Azolenprotocol (`ECLI:NL:CBB:2026:200`), the extended authorisation of *Gazelle* and its
  toxicity to bees (`ECLI:NL:CBB:2026:248`).
- **Planning and land use.** Spray zones (*spuitzones*), buffer zones and drift in permits
  and zoning plans, at first instance in the rechtbanken and on appeal at the Afdeling
  bestuursrechtspraak van de Raad van State (`ECLI:NL:RVS:2026:929`).
- **Enforcement.** Orders under penalty and administrative fines against growers in the
  rechtbanken (`ECLI:NL:RBNNE:2026:2379`, lily growers), with prosecutions under the Wet op
  de economische delicten running through the criminal chain to the Hoge Raad.
- **Residues, food and veterinary safety.** Maximum-residue-level and residue cases, reaching
  the gerechtshoven (`ECLI:NL:GHSHE:2026:1398`, residues in eggs) and the CBb by both the
  administrative and the criminal route.

Most of this is first-instance work, which is why the tracker reads the whole portal rather
than the apex courts alone.

**Not covered.** Decisions of the Ctgb itself, which are administrative acts rather than case
law and are published elsewhere; the objection stage (*bezwaar*) before an authority, for the
same reason; judgments the portal does not publish (§5); and arbitral awards, which appear
only where a court has reviewed one.

---

## 2. Where the data comes from

`data.rechtspraak.nl` is the open data service of the Raad voor de rechtspraak, and it is the
only source read for this jurisdiction. It offers a search feed for finding decisions, an
endpoint that returns a single decision as XML, and the controlled vocabularies of courts,
legal areas and procedure types that the tracker uses for its own reference data.

The portal publishes **3,737,898 decisions**, of which **949,461 carry a document body**. The
tracker fetches those with a body. The rest are registrations: an ECLI and a short block of
metadata, with no summary and no text. Nothing in them can be read for content, so no
selection method could reach them. A registration that later gains a text is picked up on a
later run, because the source's modification date advances when it does.

Each decision arrives as a Dublin Core metadata block, an optional *inhoudsindicatie* (the
court editor's summary) and the judgment or opinion itself. All of it is kept, including the
original XML, so later classification work never has to ask the courts for the same judgment
again.

The tracker re-reads the portal weekly, by modification date, and stores the source's own
revision marker for each decision. A decision that has not changed is not fetched again, and
a decision the court revises is fetched and updated in place.

**What the source does not offer.** There is no full-text search, so a topical query for
pesticides cannot be sent to the portal and selection has to be done on the retrieved text
(§3). There is no usable topical index either: the portal classifies by *rechtsgebied* —
administrative, civil, criminal, international — and pesticide litigation runs through all
four. The portal does not say why any given decision was selected for publication.

---

## 3. How cases are selected

Selection works the same way in every jurisdiction. Each fetched decision is searched for the
names of the instruments on that jurisdiction's legislation list, and it is selected when any
of them is named in its title, summary, subject field or text. There is no score and no
threshold, and nothing is marked for review automatically: an instrument either belongs on
the list or it does not (`docs/CORE_DOCUMENT.md` §2.14). The list is not narrowed to improve
precision, because a missed judgment is the expensive error (§2.7).

**The list.** `data/legislation/nl.json` holds 2,276 instruments at version 1.0.0. The Dutch
national instruments are hand-curated from what Dutch courts actually cite, measured over the
whole Rechtspraak mirror of 945,823 documents: the Wet gewasbeschermingsmiddelen en biociden
with its Besluit and Regeling, the Bestrijdingsmiddelenwet 1962 and the decrees and
regulations made under it — the Regeling toelating bestrijdingsmiddelen 1995, the Besluit
milieutoelatingseisen bestrijdingsmiddelen, the Besluit uniforme beginselen
gewasbeschermingsmiddelen and the rest. Beside them stand the Union base instruments of
pesticide law — Regulation (EC) No 1107/2009 and its predecessors, Implementing Regulation
(EU) No 540/2011, Regulation (EC) No 396/2005, the biocides directive and regulation,
Directive 2009/128/EC, Regulation (EC) No 1185/2009 — and, generated from CELLAR rather than
typed, the 2,245 acts made under them. Instruments that were considered and left off, the
Wet op de economische delicten and the Activiteitenbesluit among them, are listed with the
reason in `data/legislation/README.md`.

**Why these instruments.** Two things make a Dutch list a Dutch list.

*Legal system.* The statute and its decrees are the sharpest signals available and have no
equivalent anywhere else. Dutch pesticide litigation is authorisation, enforcement and
planning under the Wgb, and prosecution under the Bestrijdingsmiddelenwet before it, and a
judgment in any of those families names the instrument it applies.

*Language.* Dutch courts cite the Union instruments in Dutch — *Verordening (EG) nr.
1107/2009*, *de Gewasbeschermingsverordening* — and quote them in English, so every Union
instrument carries both forms, and a number matches in either. Dutch builds compounds, so a
statute's name is matched inside longer words: *Bestrijdingsmiddelenwet* inside
*Bestrijdingsmiddelenwetgeving*. The customary abbreviations — *Wgb*, *Bgb*, *Rgb*, *Bmb* —
are matched with their casing, so that the same letters in another sense are not the statute.

**What a test run measured.** «MEASURED: which store or period the legislation list was run
over, how many Dutch decisions were read and how many were selected». «MEASURED: how many of
a hand-read sample were pesticide cases, and which instruments selected the ones that were
not». «MEASURED: which cases the keyword method (branch 0.1.0, 3,027 Dutch cases on 29
August 2026) held that this list does not, and which it adds, on a hand-read». Recall cannot
be measured from a run alone — there is no reference list of Dutch pesticide judgments to
check against — but the shape of what is missed is known: a case that names none of the
listed instruments (§5).

---

## 4. Documented exceptions

The legislation method has one rule beyond the shared method, and it applies in every
jurisdiction: a year/number instrument number — every directive and decision, and
regulations from 2015 — is never matched bare, because `2009/128` is how Dutch case-law
reporters cite judgments (*NJ 2009/128*, *AB 2015/408*). Such an instrument is matched in its
bracketed, suffixed and worded forms instead: `(EU) 2017/2324`, `2009/128/EG`, *Richtlijn
2009/128*. What it costs: a judgment citing such an act by its bare number and nothing else
would be missed. None has been seen; the Official Journal does not write a number that way.

One exception about fetching survives from the keyword method: **registrations with no
document body are not fetched.** They contain no text at all — 42 of 42 sampled across 2015,
2020 and 2026 had neither summary nor judgment, and the share is steady across eleven years,
so they are not a publication backlog — and nothing in them could name an instrument. A
registration that later gains a text enters on a later run.

The ten content rules this section recorded under the keyword method — the *drift* idiom,
the toxicology sentence, *hennepkwekerij*, `CTB`, *toelatingsbesluit*, the register
abbreviations, *maneb*, *koper* and the English substance names — no longer exist. None of
those words is on a legislation list. They are preserved on branch `0.1.0`.

---

## 5. Known limits

1. **The portal publishes a selection of Dutch judgments, not all of them.** How much is left
   out cannot be established from the source. The tracker's Dutch holdings are not a census
   of Dutch pesticide litigation.
2. **Registrations without a document body are not held** (§4). They are the majority of
   published ECLIs, and they contain no text.
3. **A pesticide case that never names the legislation is missed.** A spray-drift dispute
   decided in nuisance or planning law, or a prosecution charged under the Wet op de
   economische delicten alone, can be about pesticides without citing the Wgb, and the
   tracker does not hold it. How many there are: «MEASURED: count and character of the
   cases the 0.1.0 corpus held that the legislation list does not select».
4. **Recall has never been measured against a reference list.** There is no list of Dutch
   pesticide judgments for any period to check the tracker against. The comparison with the
   keyword corpus counts what changed between two methods, not what either missed.
5. **Precision is «MEASURED: share of a hand-read sample that is pesticide litigation».** A
   wrong case is corrected by taking an instrument off the list, with the reason recorded,
   not by a threshold.
6. **Caribbean cases cannot yet be filtered out by a user.** They are identifiable in the
   stored data, but no search filter exposes the distinction, so a researcher receives
   judgments to which EU pesticide law does not apply without being able to separate them.
7. **The list carries Dutch and English names only.** A Frisian-language judgment would
   match on an instrument number, which is the same in any language, but not on a statute
   written out in Frisian. Whether the corpus contains any has not been investigated.
8. **The corpus was rebuilt under the legislation method on «MEASURED: date of the rebuild»**
   and holds «MEASURED: n cases». The figures in §3 come from that rebuild and from the
   comparison with the corpus of 29 August 2026.
