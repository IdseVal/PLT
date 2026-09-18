# Legislation lists

Stage 1 of the PLT ingestion filter chain. See
[`docs/CORE_DOCUMENT.md` §2.5](../../docs/CORE_DOCUMENT.md#25-selection-by-legislation-and-per-jurisdiction-legislation-lists)
for why this exists and [§2.14](../../docs/CORE_DOCUMENT.md#214-selection-is-a-search-for-the-names-of-pesticide-laws)
for the decision that made it what it is.

## Files

| File | Purpose |
| --- | --- |
| `schema.json` | JSON Schema (2020-12) every list validates against. Schema `1.0.0`. |
| `nl.json` | Netherlands — Dutch, with the English forms of every instrument as aliases. |
| `eu.json` | European Union — English, French, German, Dutch. |

**One file per jurisdiction, named `<jurisdiction-code-lowercase>.json`.** A jurisdiction
cannot be onboarded to the pipeline until its list exists: the national instruments and
the citation forms are specific to a legal order and a language and do not transfer.

## The rule

**A case is selected when any instrument on its jurisdiction's list is named in its title,
abstract, subject fields or full text.** That is the whole rule. There is no score, no
threshold, no gate and no exclusion pattern. An instrument either belongs on the list, in
which case a judgment that names it is a pesticide case, or it does not.

Before adding an instrument, ask the only question that matters:

> Would a judgment that names this instrument, and nothing else on this list, be a case the
> tracker should hold?

If the answer is no, the instrument does not go on. That is what keeps the general
chemicals, water and food-law instruments off (see *Considered and left off*).

## What an entry is

Every entry is one instrument. The matcher calls it a term, because to the matcher it is a
name and the spellings it is cited under.

| Field | What it holds |
| --- | --- |
| `id` | Stable identifier, `<lang>-<slug>`. Match provenance is stored against it and it labels every case the instrument selected, so never reuse one for a different instrument. |
| `term` | The instrument's short citation as a court writes it, in the list's language: `Regulation (EC) No 1107/2009`, `Wet gewasbeschermingsmiddelen en biociden`. **This is the public label.** |
| `label` | What to show a reader instead of `term`. Required when `match` is `regex`, because a pattern is not a name. |
| `aliases` | Every other form the instrument is cited under: its number in the forms courts cite it under, its title in each language the list carries, its customary short name. An alias reports the term's id and label, so **a different instrument is never an alias**. |
| `category` | The kind of instrument: `regulation`, `directive`, `decision`, the `implementing_*` and `delegated_*` variants, `national_act`, `national_decree`, `national_ministerial_regulation`. The second public label. |
| `match` | `phrase` for citations and titles, `substring` for a Dutch name that appears inside compounds, `word` for a single token, `regex` for what the others cannot express. |
| `case_sensitive` | Only for acronyms. Inherited by every alias, so a case-sensitive term carries acronyms only; the loader refuses anything else. |
| `celex`, `bwb`, `title` | Provenance for the reader of the list. Nothing matches on them. |
| `derived_from` | Present on every entry the generator wrote; absent from every hand-curated one. That key is how the generator tells its own entries from the curator's. |
| `note` | Why the instrument is here, and any measurement behind a matching decision. |

## Two parts: curated and generated

**The base instruments are hand-curated.** They are the instruments pesticide law hangs
off, and each carries its bare number where that number is unambiguous, its number behind
its type word in each language, and the short names courts use for it:

| Instrument | What it governs |
| --- | --- |
| Directive 79/117/EEC, Directive 91/414/EEC, **Regulation (EC) No 1107/2009** | Plant protection products: the two repealed instruments and the one in force. |
| Implementing Regulation (EU) No 540/2011 | The register of approved active substances. Every approval, renewal and non-renewal amends its Annex. |
| Regulation (EC) No 396/2005 | Maximum residue levels. |
| Directive 98/8/EC, **Regulation (EU) No 528/2012** | Biocidal products, repealed and in force. The Dutch statute governs both product families in one act, which is why biocides are in scope. |
| Directive 2009/128/EC | Sustainable use of pesticides. |
| Regulation (EC) No 1185/2009 | Statistics on pesticides. |

For the Netherlands the national instruments are hand-curated too. They were taken from
what Dutch courts actually cite, measured over the whole Rechtspraak mirror (945,823
documents): every statute reference the portal records, and every mention of a *wet*,
*besluit* or *regeling* whose title names bestrijdingsmiddelen, gewasbeschermingsmiddelen
or biociden. The Wet gewasbeschermingsmiddelen en biociden and the Bestrijdingsmiddelenwet
1962 account for most of it; the decrees and regulations made under them are the rest. The
customary abbreviations — `Wgb`, `Bgb`, `Rgb`, `Bmb`, and their all-capitals renderings —
are carried as case-sensitive expressions in terms of their own, because a mixed-case
literal cannot satisfy the acronym-only rule and a lowercase rendering is not the statute.
Measure before deciding a casing is unwritten: the first full run missed 49 judgments that
write `WGB`.

**Everything made under the Union base instruments is generated, not typed.** CELLAR
records which acts are *based on*, *amend* or *correct* each instrument, and
[`backend/scripts/legislation_from_cellar.py`](../../backend/scripts/legislation_from_cellar.py)
asks it, one query per base instrument, and writes one entry per act with its number in
every citation form. A list of two thousand implementing regulations written from memory
would be a list with a recall floor.

```bash
cd backend
python scripts/legislation_from_cellar.py --cache .cache/cellar ../data/legislation/eu.json ../data/legislation/nl.json
```

The cache keeps CELLAR's answers between runs; `--refresh` asks again. A run replaces every
entry carrying `derived_from` and touches none without it, records the date in `sources`,
and bumps nothing: whether the regenerated list changes what is selected is the curator's
call, and `list_version` is the curator's to bump.

What the generator keeps is legislation — CELEX sector 3, of the regulation, directive and
decision types EUR-Lex distinguishes, whether or not still in force. It drops proposals,
reports, resolutions and communications, which a judgment does not cite as the law it
applies, and corrigenda, whose number is the number of the act they correct. It also drops
an act related to a base instrument only by amending it when that act amends more than one
instrument in all. Such an act is about something else: Regulation (EC) No 1882/2003 adapts
the committee procedures of a hundred acts, Directive 2004/66/EC adapts directives to the
2004 accession, Directive 2007/47/EC is about medical devices and also amended the biocides
directive, Regulation (EU) 2019/1009 is about fertilisers and also amended 1107/2009, and a
judgment citing one is about that. An act made under a base instrument amends at most that
instrument and its register. Ten such acts are left out at version 1.0.0.

## Citation forms

An act is cited by its number far more often than by its title, and the number is written
differently depending on when the act was adopted and in which language. The generator
carries every form; a hand-curated entry should too.

**Number/year — regulations before 2015.** `540/2011`, `1107/2009`. Written the same in
every language, and not a form any case-law reporter uses — but it is the form of a national
law (*Law No 116/2014*), a docket (*Case R 520/2011-4*), a bulletin and a date, and the
first run of a list that carried generated acts bare selected cases on each of those. So the
**hand-curated base instruments carry their bare number**, each on the curator's
measurement, and **a generated act carries its number behind its bracket token only**, in
each language's spelling: `(EU) No 116/2014`, `(EU) nr. 116/2014`, `(UE) no 116/2014`,
`(EU) Nr. 116/2014`. Nothing but the act is written that way.

**Year/number — every directive and decision, and regulations from 2015.** `2017/2324`,
`2009/128`. **The bare number is never carried**, because it is exactly how Dutch case-law
reporters cite judgments: *NJ 2009/128* is a Supreme Court judgment, *AB 2015/408* an
administrative one, and a list that matched on the bare number would select them. What is
carried instead:

- the worded forms in each language the list carries — `Directive 2009/128`,
  `Richtlijn 2009/128`, `Uitvoeringsverordening 2017/2324`, `Durchführungsverordnung
  2017/2324` — and their plurals, `Directives 2009/128`;
- the worded and bracketed forms, in each language's own token — `Implementing Regulation
  (EU) 2017/2324`, `Uitvoeringsverordening (EU) 2017/2324`, `règlement d'exécution (UE)
  2017/2324`.

**Never the suffixed or bracketed number without its type word.** Directives, decisions and,
from 2015, regulations share one numbering space: `2003/35/EC` is Commission Decision
2003/35/EC recognising pesticide dossiers and Directive 2003/35/EC on public participation,
`2009/65/EC` a non-inclusion decision and the UCITS directive, `2003/5/EC` a deltamethrin
directive and a cartel decision. The first run of a list that carried the suffixed forms
selected 119 cases on the public-participation directive alone. Only the type word tells the
acts apart, and the Official Journal and the courts always write it.

`Directive 2003/5` reaches `Directive 2003/5/EC` because the slash after the number is not a
word character, and `Regulation (EU) 2017/2324` reaches `Implementing Regulation (EU)
2017/2324` because a space before it is a boundary. The trie compiles all of it — forty thousand
literals for the EU list — into two patterns, so the cost of a scan is the length of the
text and not the size of the list: the EU list loads in under a second and reads a megabyte
of judgment in about twenty-five milliseconds.

**Titles and short names** are carried for the base instruments only, in the list's
languages: a court writes *de Gewasbeschermingsverordening* or *the Biocidal Products
Regulation*, and never writes an implementing act's title without its number.

## Match modes

- `phrase` is what a citation wants: a whitespace-normalised sequence matched on word
  boundaries where its edges are word characters, so `1107/2009` does not match inside
  `11107/2009`, and a line break inside a citation is still a space.
- `substring` is for a Dutch name that appears inside compounds —
  `Bestrijdingsmiddelenwet` inside *Bestrijdingsmiddelenwetgeving*. The loader refuses any
  substring literal under six characters, term or alias, because `match` is inherited by
  every alias and a short fragment is reached inside words that have nothing to do with it.
- `word` is for a single token that must not be found inside another.
- `regex` is for the acronyms. `(?<!\w)Wgb(?!\w)`, case-sensitive, matches the statute's
  abbreviation and neither `wgb` nor `WGB`; a regex term names its `label`, and the acronym
  rule does not reach an expression.

## Case sensitivity

**`case_sensitive` applies to a term and every one of its aliases.** The loader enforces
that a case-sensitive term carries acronyms only — no lowercase letter, no space — because
an ordinary word inheriting the flag silently loses every rendering but the curated one,
sentence-initial included. `case_sensitive_exception` opts a term out of that rule and is
deliberately awkward: refused on a term that is not case-sensitive, and on one that does
not need it. No shipped term uses it.

## Considered and left off

The half of an inclusion criterion a systematic review is asked for. These were considered
and are not on the lists, each for the same reason: a judgment that names only this
instrument is not a pesticide case.

| Instrument | Why it is off |
| --- | --- |
| Regulation (EC) No 1907/2006 (REACH), Regulation (EC) No 1272/2008 (CLP) | Govern industrial chemicals generally. Under the keyword lists REACH and ECHA between them carried twelve of fifty-four selected EU cases in 2024, including one on lead in ammunition. |
| Directive 2000/60/EC (Water Framework), Directive 2006/118/EC (groundwater), Kaderrichtlijn Water | Govern water quality generally. |
| Regulation (EC) No 1367/2006 (Aarhus) | Governs access to environmental information in every field. |
| Regulation (EC) No 178/2002 (general food law), Regulation (EU) 2017/625 (official controls) | Govern food and feed generally; residue cases name 396/2005. |
| Wet op de economische delicten, Warenwet, Wet milieubeheer, Activiteitenbesluit milieubeheer, Wet milieugevaarlijke stoffen | General Dutch instruments under which pesticide offences are prosecuted or pesticide use is regulated among much else. The Economic Offences Act alone selected 577 unrelated cases under the keyword lists. |
| Lozingenbesluit open teelt en veehouderij | Governs discharges from open cultivation, of which spray drift is one; its title does not name pesticides and a case under it need not concern them. |
| The temporary exemptions under article 38 Wgb (*Tijdelijke vrijstelling …*) | Hundreds of ministerial regulations, each naming one product and one crop. A case about one names the Wgb. |

## Adding a jurisdiction

1. Copy the closest existing list for structure only — not for instruments.
2. Set `jurisdiction`, `jurisdiction_name`, `languages`, and `list_version` to `1.0.0`.
3. Hand-curate the national instruments from what that jurisdiction's courts cite: the
   statute governing authorisation and use, its decrees and regulations, and its repealed
   predecessors, each in the working language(s) of the courts with the English rendering
   as an alias where one exists.
4. Add the Union base instruments in that language's citation forms, then run the
   generator to add everything made under them.
5. Validate against `schema.json`, run the pipeline in dry-run mode over the store and read
   the match report before enabling ingestion. Record what it measured in
   `docs/jurisdictions/<code>.md` §3.

## Curation

These lists are **data curated by the content manager**, not code. Every ingestion run
records which instrument ids matched each case, and now also which list it applied — the
version the file claimed and the SHA-256 of the file — so a corpus can say exactly what
produced it.

**Bump `list_version` on every change that can alter what is selected or how it is
labelled** — an instrument, an alias, a category, a match mode. Bump the minor version when
instruments are added or removed, the major version when matching semantics change, the
patch version for a change that cannot alter selection. Note the reasoning in `notes`.

**Do not bump it for a change that cannot** — a corrected title, a rewritten note, a typo
in prose. `keyword_match` records the version that produced each match, and two versions
with identical matching behaviour make that record ambiguous.
