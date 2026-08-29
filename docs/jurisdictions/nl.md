# Netherlands — how cases are collected

| | |
| --- | --- |
| **Jurisdiction code** | `NL` |
| **Courts covered** | Every court publishing through the Raad voor de rechtspraak, at all instances |
| **Source** | Rechtspraak open data portal, `data.rechtspraak.nl` |
| **Keyword list** | `data/keywords/nl.json`, which records its own version |
| **Status** | Connector built and tested against the live service; no cases stored yet |
| **Source last checked** | 4 August 2026 |
| **Last reviewed** | 6 August 2026 |

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

Selection works the same way in every jurisdiction. Each fetched decision is scored against
that jurisdiction's keyword list; a decision reaching the threshold is selected, and one
scoring just above it is additionally marked for a content manager to confirm or reject. The
threshold is not raised to improve precision, because a missed judgment is the expensive
error (`docs/CORE_DOCUMENT.md` §2.7).

**The list.** `data/keywords/nl.json` holds the terms, all of them Dutch; read the file for
the count, which changes with every curation pass. Most of it is the active substances of
the Ctgb authorisation register, taken from the register rather than chosen and including
the ones no longer authorised. The rest is the statutes, the authorisation vocabulary, the
crops and the spraying practices described below. Nearly every term qualifies a decision on
its own; a handful are contextual. A decision is selected at a score of 3 or more, and one
scoring below 5.5 is marked for review. Terms matching in the title or the summary count for
half as much again as terms in the body.

**Why these terms.** Three things make a Dutch list a Dutch list.

*Language.* Dutch builds compounds, so the terms one wants to match sit inside longer words —
*gewasbeschermingsmiddelenrichtlijn*, *bestrijdingsmiddelengebruik*. Most of the list
therefore matches on word fragments rather than whole words. That is what gives the list its
reach, and it is also the reason for most of the exceptions in §4.

*Legal system.* The national instruments are the sharpest signals available and have no
equivalent anywhere else: the Wet gewasbeschermingsmiddelen en biociden with its Besluit and
Regeling, the older Bestrijdingsmiddelenwet, the Ctgb and its full name, the authorisation
vocabulary (*toelating*, *toelatingshouder*, *wettelijk gebruiksvoorschrift*), the Article 38
exemption and *noodtoelating*, and the Wet op de economische delicten. The EU instruments
appear in their Dutch citation forms, though their numbers match in any language.

*Agronomy.* The crops and practices that generate Dutch pesticide litigation: lily and bulb
growing, tree nurseries, orchards, arable and glasshouse cultivation, and the spraying
vocabulary — *bespuiting*, *spuitzone*, *spuitdrift*, *afdrift*, *teeltvrije zone*. These
carry the lowest weight, because a crop alone is not a pesticide case.

**What a test run measured.** Over June 2026 the tracker read 10,011 Dutch decisions and 38
passed the filter. All 38 were read by hand: 10 were unambiguously pesticide litigation, 6
were adjacent and a matter of judgement — a PFAS class action, residues in eggs, a biocide
case, two slaughterhouse fines and glyphosate used against Japanese knotweed — and 22 were
not pesticide cases.

The score separates the two groups. Of the 15 decisions scoring 5.0 or more, every one was a
clear or an adjacent case. Of the 23 scoring between 3.0 and 4.5, all but two were wrong.
That is why the borderline band is reviewed rather than rejected: the false positives sit in
one place, and review is aimed at it.

Recall cannot be measured from this run — there is no list of the Dutch pesticide judgments
of June 2026 to check against. What can be said is that the cases the run was expected to
find were found: the CBb decisions on Ctgb authorisations, the lily and bulb disputes, the
spray-zone planning appeals. A further 61 decisions scored between 2.0 and 3.0 and are the
obvious sample for a first proper measurement of what the threshold costs.

---

## 4. Documented exceptions

Ten rules apply to this jurisdiction beyond the shared method. The first decides which
documents are fetched at all, and the second rejects a document on its content. Six of them
only narrow how a term matches, so a decision that mentions pesticides anywhere else still
scores normally, and the last two keep words out of the list altogether.

| What it excludes | Why | What it costs |
| --- | --- | --- |
| Registrations with no document body | They contain no text at all: 42 of 42 sampled across 2015, 2020 and 2026 had neither summary nor judgment. The share is steady across eleven years, so they are not a publication backlog | Nothing that could be selected. A registration that later gains a text enters on a later run |
| Documents containing *"in een opwelling van drift"* | *Drift* in Dutch is both spray drift and a fit of anger, and this is the standard criminal-law idiom for the second | A pesticide judgment quoting the idiom would be discarded. None has been seen. This is the bluntest rule in the list: it discards the document whatever else it says |
| *bestrijdingsmiddel* inside the phrase *"geneesmiddelen, drugs en/of bestrijdingsmiddelen"* | The standard sentence of a Dutch toxicology screen, which admitted homicide judgments | Only that occurrence is ignored. A poisoning case that also names a pesticide elsewhere still scores in full |
| *kwekerij* inside a longer word, such as *hennepkwekerij* | Cannabis-cultivation judgments were matching a nursery term | Nursery cases written only as *plantenkwekerij* or *rozenkwekerij* lose one contextual point. A judgment saying *kwekerij* on its own still matches |
| `CTB` followed by *-laag* or *-lagen* | Cement-bound road base in construction disputes, sharing an abbreviation with the Ctgb's predecessor | Nothing measurable. The historical abbreviation is kept, so older judgments using it still match |
| *toelatingsbesluit* where no plant-protection term appears in the document | In immigration law a *toelatingsbesluit* is a decision admitting a foreign national, and immigration is one of the largest categories in the Dutch corpus | An authorisation judgment that uses the word and never names a plant protection product would be missed. None has been seen; the Ctgb's own name contains the required term |
| The register's abbreviations `DDAC`, `BBIT`, `TMAD`, `CIPC` and `DBNPA` inside longer words | Each was an alias of a long chemical name and matched on word fragments because the name does. `DDAC` matched inside the surname *Faddach*, `BBIT` inside *rabbits*, `TMAD` inside the place name *Westmade*: 86 decisions in 150,000 sampled, none about a plant protection product, and nothing gates them, so each was selected on the fragment alone | Nothing. Every abbreviation is kept, as a term of its own matched as a whole word. A decision naming both the abbreviation and the full name now scores twice, which takes it out of the review band |
| The substance name *maneb* inside longer words | It was an alias of *mancozeb* and matched on word fragments because that name does. Five characters is below the length at which a literal is only reached at a compound boundary, which is the same shape that put `DDAC` inside *Faddach*. Nothing gates it, so a fragment would select the decision on its own | Nothing measurable. In 150,000 sampled judgments the only word containing it is *manebhoudende*, one decision, and *maneb-houdend* still matches because a hyphen is not a word character. A decision naming both *maneb* and *mancozeb* now scores twice, which takes it out of the review band |
| The bare word *koper*, which the Ctgb register carries as an active substance | *Koper* is both copper and a buyer, and in the corpus it is the buyer: it appears in 77 of 2,000 sampled judgments, none of them about a plant protection product | Nothing measurable. The list carries sixteen unambiguous copper compounds — *kopersulfaat*, *koperoxychloride*, *koperdihydroxide* and the rest — and a judgment about a copper product names the authorised one. A judgment naming only the bare element scores three points less, which can put it in the review band |
| The English names the register carries beside the Dutch ones — *beer*, *silver*, *Iron*, *Milk*, *Quartz*, *Vinegar*, *talc*, *honey*, *iodine*, *sulfur*, *Whey*, *yeast* — with *gist*, *Diamine* and *amorf*, the fragment left when *amorphous silica* was split | An English name in a Dutch list is a homonym the Dutch word never had. *Beer* is the surname *De Beer*, *Quartz* and *Silver* are watch and shipping brands, *Milk* is a dairy company, *Whey* is Papiamento. They matched 459 of 150,000 sampled judgments between them; *beer* alone accounts for 319, and every match read was a name, a brand or an English quotation rather than the substance | Nothing measurable. A Dutch court writing about these substances writes *bier*, *zilver*, *koemelk*, and the Dutch term stays in the list; only the English spelling is gone. English names that are chemical nomenclature rather than ordinary words are kept |

Two of these rules can be defeated by a spelling the pattern does not anticipate: an extra
space before *bestrijdingsmiddelen* in the toxicology sentence, or road base written as a
bare `CTB`. Both then fail in the safe direction — the document is selected, scores at the
threshold, and is marked for review.

---

## 5. Known limits

1. **The portal publishes a selection of Dutch judgments, not all of them.** How much is left
   out cannot be established from the source. The tracker's Dutch holdings are not a census
   of Dutch pesticide litigation.
2. **Registrations without a document body are not held** (§4). They are the majority of
   published ECLIs, and they contain no text.
3. **Recall has never been measured.** There is no reference list of Dutch pesticide
   judgments for any period to check the tracker against.
4. **Precision is about one case in four on a strict reading**, with the wrong cases
   concentrated just above the threshold. Those cases are marked for review rather than
   rejected.
5. **Caribbean cases cannot yet be filtered out by a user.** They are identifiable in the
   stored data, but no search filter exposes the distinction, so a researcher receives
   judgments to which EU pesticide law does not apply without being able to separate them.
6. **The list covers Dutch only.** Frisian-language judgments, if the corpus contains any,
   would not be matched. This has not been investigated.
7. **No cases have been stored yet.** Every figure here comes from test runs against the live
   service.
