# \<Jurisdiction\> — how cases are collected

> **Using this template.** Copy it to `docs/jurisdictions/<code>.md`, lower case, with the
> same code as the legislation list in `data/legislation/`. Keep the five sections, in this order,
> in every jurisdiction: a reader who knows one document knows them all. Delete these
> guidance blockquotes as you write.
>
> Write for a researcher deciding whether the tracker's coverage of this jurisdiction can be
> relied on in published work. Short sentences, concrete numbers, no implementation detail.

| | |
| --- | --- |
| **Jurisdiction code** | `XX` |
| **Courts covered** | \<one line\> |
| **Source** | \<the publisher whose data is read, and its address\> |
| **Legislation list** | `data/legislation/xx.json`, version \<n.n.n\> |
| **Status** | *planned / connector built / ingesting* |
| **Source last checked** | \<date\> |
| **Last reviewed** | \<date\> |

---

## 1. What is covered

> Which courts, and which cases belong here rather than to a neighbouring jurisdiction. A
> case belongs to the jurisdiction of the court that decided it, so state the boundary
> against `eu.md` explicitly. Give the case identifier used as the unit of selection, say
> where pesticide litigation is actually heard in this country, and name what a reader might
> expect to find here and will not.

---

## 2. Where the data comes from

> The source, the size of what it publishes, how often it is read, and what it does not
> record. Name any field the tracker would like and the source does not have, and say what
> is done instead.

---

## 3. How cases are selected

> Every jurisdiction uses the same method: fetch each document, look for the instruments on
> the legislation list in its title, abstract, subject fields and full text, and select it
> when any is named. Describe the list — how many instruments, which are hand-curated and
> which generated, which languages the names are carried in — say why these instruments suit
> this jurisdiction, and report what a test run over a real period or the store measured: how
> many documents, how many were selected, how many were genuine on a hand-read, and which
> instruments selected the ones that were not.

---

## 4. Documented exceptions

> Anything this jurisdiction does beyond the shared method, one row or one short paragraph
> each. Every exception states what it excludes, why, and what it costs. If there are none,
> say so in a sentence.

---

## 5. Known limits

> What the tracker does not hold for this jurisdiction, so that it can be cited without
> overclaiming. Keep separate what is known to be absent, what is incomplete by an unknown
> amount, and what has not been investigated.
