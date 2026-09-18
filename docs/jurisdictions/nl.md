# Netherlands — how cases are collected

| | |
| --- | --- |
| **Jurisdiction code** | `NL` |
| **Courts covered** | Every court publishing through the Raad voor de rechtspraak, at all instances |
| **Source** | Rechtspraak open data portal, `data.rechtspraak.nl` |
| **Legislation list** | `data/legislation/nl.json`, which records its own version |
| **Status** | Corpus rebuilt under the legislation method on 17 September 2026: 624 cases |
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

**The list.** `data/legislation/nl.json` holds 2,266 instruments at version 1.0.0. The Dutch
national instruments are hand-curated from what Dutch courts actually cite, measured over the
whole Rechtspraak mirror of 945,823 documents: the Wet gewasbeschermingsmiddelen en biociden
with its Besluit and Regeling, the Bestrijdingsmiddelenwet 1962 and the decrees and
regulations made under it — the Regeling toelating bestrijdingsmiddelen 1995, the Besluit
milieutoelatingseisen bestrijdingsmiddelen, the Besluit uniforme beginselen
gewasbeschermingsmiddelen and the rest. Beside them stand the Union base instruments of
pesticide law — Regulation (EC) No 1107/2009 and its predecessors, Implementing Regulation
(EU) No 540/2011, Regulation (EC) No 396/2005, the biocides directive and regulation,
Directive 2009/128/EC, Regulation (EC) No 1185/2009 — and, generated from CELLAR rather than
typed, the 2,235 acts made under them. Instruments that were considered and left off, the
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
are matched in the two casings courts write them in, *Wgb* and *WGB*, and not in lowercase,
so that the same letters in another sense are not the statute. The first full run found 49
judgments writing *WGB* that mixed case alone had missed; list 1.1.0 carries both.

**What the rebuild measured.** On 17 September 2026 the list was run over the whole mirror:
945,823 documents, decisions from 1994 to 8 August 2026, of which 624 named an instrument
on the list — one in 1,500. The Wet gewasbeschermingsmiddelen en biociden selected 249 of
them and its abbreviation *Wgb* 156, the Bestrijdingsmiddelenwet 1962 206, Verordening (EG)
nr. 1107/2009 205, Richtlijn 91/414/EEG 142, Verordening (EU) nr. 528/2012 76, the Regeling
toelating bestrijdingsmiddelen 1995 55 and the Besluit milieutoelatingseisen
bestrijdingsmiddelen 42 with its abbreviation 39. The College van Beroep voor het
bedrijfsleven, which hears the authorisation appeals, supplies most of the corpus.

The keyword method (branch `0.1.0`), run over the same mirror the same day, selected 3,027
— exactly the corpus published on 29 August 2026, which is what reproducibility from the
mirror means in practice. The two methods agree on 546 cases. The keyword method held 2,481
that this list does not select, and this list selects 78 the keyword method did not.

*What was lost.* The 2,481 were selected by the words *bestrijdingsmiddel* (1,086 of them),
*gewasbeschermingsmiddel* (695), *spuitzone* (223), *bespuiting* (192), *pesticide* (112),
*biocide* (97), *teeltvrije zone* (96), *maximale residulimiet* (88), *insecticide* (85) and
*herbicide* (80), and by substance names. Forty were read from their titles, labels and
matched passages. About three in five are not pesticide litigation: a criminal judgment
whose toxicology report rules pesticides out, a substance name that is also a tax, customs
or chemical-industry matter (*chroomtrioxide*, *natriumhypochloriet*, *wijnsteenzuur*,
*ethyleenoxide*), a tenancy or employment dispute in which spraying is mentioned once.
About two in five are pesticide litigation that never names the legislation, and these are
the real cost: the Raad van State's planning appeals about *spuitzones* between orchards and
homes, which are decided under planning law and cite no pesticide instrument; enforcement
under the Wet op de economische delicten charged without naming the Wgb; and civil disputes
about spray drift decided in nuisance. On the sample that is of the order of 1,000 cases,
and §5 records it as the method's first limit.

*What was gained.* Of the 78, nine are pesticide cases the keyword lists had missed,
selected on the abbreviations *Bmb* and *Bgb* of the Besluit milieutoelatingseisen
bestrijdingsmiddelen and the Besluit gewasbeschermingsmiddelen en biociden. The rest are
errors in the judgments themselves that a text method cannot see past: twenty-four
temporary-protection judgments of the Rechtbank Den Haag that write the Temporary Protection
Directive 2001/55/EG as *Richtlijn 2011/55/EG*, the number of a Commission directive that
included a substance in Annex I to 91/414/EEG; eleven that write the Return Directive
2008/115/EG as *richtlijn 2008/15*; eight that write the Working Time Directive 2003/88/EG
as *2003/84*. Every one of those numbers is a Commission directive made under 91/414/EEG,
and the Dutch courts almost never cite those directives themselves; whether they stay on the
list is a curation decision the Law group can take on this evidence.

*Precision.* Forty cases were drawn at random from the 624 and read from their titles and
matched passages: 35 are pesticide or biocide litigation, five were admitted by a
mis-typed citation of the kind above. That reading was made by the project's assistant from
titles and passages, not by a lawyer from the judgments; the Law group's own reading
replaces it when made. Recall cannot be measured from a run alone — there is no reference
list of Dutch pesticide judgments to check against — but the shape of what is missed is
known: a case that names none of the listed instruments (§5).

---

## 4. Documented exceptions

The legislation method has one rule beyond the shared method, and it applies in every
jurisdiction: a year/number instrument number — every directive and decision, and
regulations from 2015 — is never matched bare, because `2009/128` is how Dutch case-law
reporters cite judgments (*NJ 2009/128*, *AB 2015/408*), and never as a bare suffixed or
bracketed number either, because directives, decisions and, from 2015, regulations share
one numbering space and only the type word tells them apart. Such an instrument is matched
behind its type word instead, in Dutch and in English: *Uitvoeringsverordening (EU)
2017/2324*, *Richtlijn 2009/128*, *Directive 2009/128/EC*. What it costs: a judgment citing
such an act by its number alone would be missed on that citation. None has been seen; a
Dutch court writes the type word.

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
   tracker does not hold it. The comparison with the keyword corpus puts a floor under the
   number: of the 2,481 cases that corpus held and this one does not, about two in five are
   pesticide litigation on a reading of a sample (§3) — of the order of 1,000 cases, most
   of them the Raad van State's spray-zone planning appeals and enforcement charged under
   the Wet op de economische delicten. A researcher working on spray zones should not rely
   on this jurisdiction's holdings.
4. **Recall has never been measured against a reference list.** There is no list of Dutch
   pesticide judgments for any period to check the tracker against. The comparison with the
   keyword corpus counts what changed between two methods, not what either missed.
5. **Precision is about seven in eight** on a reading of forty random cases (§3). The wrong
   cases have one cause: a citation mis-typed in the judgment that lands on the number of a
   Commission directive made under 91/414/EEG — forty-three cases in the corpus, most of
   them asylum and immigration judgments. A wrong case is corrected by taking an instrument
   off the list, with the reason recorded, not by a threshold.
6. **Caribbean cases cannot yet be filtered out by a user.** They are identifiable in the
   stored data, but no search filter exposes the distinction, so a researcher receives
   judgments to which EU pesticide law does not apply without being able to separate them.
7. **The list carries Dutch and English names only.** A Frisian-language judgment would
   match on an instrument number, which is the same in any language, but not on a statute
   written out in Frisian. Whether the corpus contains any has not been investigated.
8. **The corpus was rebuilt under the legislation method on 17 September 2026** and holds
   624 cases. The figures in §3 come from that rebuild and from the
   comparison with the corpus of 29 August 2026.
