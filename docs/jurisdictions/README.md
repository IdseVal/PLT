# Jurisdiction documents

Every jurisdiction in the Pesticide Litigation Tracker has a document here describing how its
case law is collected. They are written for the reader of the database, not for the developer
of the pipeline: a researcher who finds a case in the tracker — or, more importantly, fails
to find one — should be able to establish from these pages which courts were searched, from
which source, against which terms, and what the tracker is known not to hold.

| Code | Jurisdiction | Document | Keyword list | Source last checked |
| --- | --- | --- | --- | --- |
| `NL` | Netherlands | [`nl.md`](nl.md) | `data/keywords/nl.json` | 4 August 2026 |
| `EU` | European Union | [`eu.md`](eu.md) | `data/keywords/eu.json` | 4 August 2026 |

The EU is a jurisdiction in its own right, never a total of its member states
(`docs/CORE_DOCUMENT.md` §3.3). A case decided by the Court of Justice is an `EU` case; a
case decided by a Dutch court applying Regulation (EC) No 1107/2009 is an `NL` case. Neither
document counts the other's cases.

## The structure

Every document uses the same five sections, so that a reader who knows one knows them all:

1. **What is covered** — the courts, the boundary against neighbouring jurisdictions, and
   what is not there.
2. **Where the data comes from** — the source, its size, and what it does not record.
3. **How cases are selected** — the keyword list, and what a test run over a real period
   measured.
4. **Documented exceptions** — anything the jurisdiction does beyond the shared method.
5. **Known limits** — what the tracker does not hold, so it can be cited without
   overclaiming.

[`TEMPLATE.md`](TEMPLATE.md) is that structure, ready to fill in. The requirement for these
documents is `docs/CORE_DOCUMENT.md` §2.9; the rule that selection works identically
everywhere, and that anything else is an explicit documented exception, is §2.10.

## Adding a jurisdiction

1. Copy `TEMPLATE.md` to `<code>.md`, lower case, matching the keyword list file name.
2. Write §1 and §2 from primary sources on the court system and from the source's own
   service. This is the part that cannot be derived from an API, and getting it wrong
   produces no error — it produces a jurisdiction that looks covered and is not.
3. Record the access route, checked against the live service, and add its summary to Annex 2a
   of the core document.
4. Write the keyword list (`data/keywords/README.md`), run it over a sample period, and
   report what it measured in §3.
5. Record any exception in §4 with its cost. Keyword lists are curated by the content
   manager (`docs/CORE_DOCUMENT.md` §2.3), so a jurisdiction document presents the evidence
   and the trade; it does not decide.
