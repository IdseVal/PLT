# \<Jurisdiction\> — methodology

> **How to use this template.** Copy it to `docs/jurisdictions/<code>.md`, lower-case, with
> the same code as the keyword list in `data/keywords/`. Replace every placeholder; delete
> nothing. The guidance in each section is set as a blockquote and should be **removed** once
> the section is written — it explains what belongs there and why, not what to say.
>
> The five numbered requirements of `docs/core-document.md` §2.9 map onto §2 (where the
> litigation is), §3 (how to reach it), §4 (the keyword list), §5 (documented exceptions) and
> §6 (known limitations) below. A document missing one of those has not been written; it has
> been started.
>
> Write for a legal-academic reader. The audience is a researcher deciding whether this
> tracker's coverage of this jurisdiction can be relied on in published work, not a developer
> looking for an implementation note. Prefer "the Court of Justice delivered 501 judgments in
> 2024, of which 54 passed the filter" to "discovery yields candidates".

| | |
| --- | --- |
| **Jurisdiction code** | `XX` |
| **Jurisdiction** | \<name\> |
| **Status** | *planned / connector in development / ingesting* |
| **Keyword list** | `data/keywords/xx.json`, version \<n.n.n\> |
| **Connector** | `plt.pipeline.connectors.<module>` (or *none yet*) |
| **Endpoints last verified** | \<date\>, against the live service |
| **Document last reviewed** | \<date\> |
| **Author / reviewer** | \<role\> |

---

## 1. Scope

> One paragraph: what this document asserts the PLT holds for this jurisdiction, and what it
> does not. State the boundary against neighbouring jurisdiction documents explicitly —
> above all against `eu.md`, since EU pesticide law is litigated in national courts and
> national implementations are litigated at the Court of Justice. A case belongs to exactly
> one jurisdiction: the one whose court decided it.

---

## 2. Where the litigation is

> **This is the section that cannot be derived from an API, and the one that decides whether
> the jurisdiction is genuinely covered.** Getting it wrong produces no error: it produces a
> jurisdiction that looks covered and is not (§2.9).
>
> Annex 2 of the core document lists apex courts almost exclusively. Most pesticide
> litigation never reaches an apex court — authorisation challenges, spray-drift disputes,
> planning appeals over spray zones, residue prosecutions and enforcement orders are largely
> first-instance and often specialised. Sweden's Land and Environment Courts are the worked
> example in Annex 2 itself. Establish where these cases actually go **before** deciding what
> the connector fetches.

### 2.1 The court system, as it bears on pesticide cases

> Set out the instances and the specialised jurisdictions, and say for each what kind of
> pesticide case reaches it: authorisation and its withdrawal; enforcement and penalties;
> planning and land use; civil liability and mass claims; criminal prosecution; residues and
> food safety. Name the forum for authorisation decisions specifically — in most member
> states that is a single specialised court, and it is where the highest-value cases sit.
>
> Cite primary sources. Where a claim rests on observation rather than statute — "the two
> clearest authorisation cases in the dry run were both decided by X" — say so in those words
> rather than dressing an observation up as a rule.

### 2.2 What Annex 2 lists, and how this differs

> A short, explicit reconciliation. Annex 2 is a starting point per member state, not a map
> of where pesticide cases are heard, and every divergence found while writing this document
> should be stated here and, where it changes what the pipeline reaches, proposed as an
> Annex 2 amendment. Do not silently improve on Annex 2 in code.

### 2.3 Out of scope for this jurisdiction

> What a reader might reasonably expect to find here and will not: administrative appeal
> bodies that are not courts, arbitration, unpublished first-instance decisions, courts of
> territories with a different constitutional relationship to the EU, cases decided before
> the source's publication coverage begins.

---

## 3. How to reach it

> Requirement 2 of §2.9. **Every fact in this section carries the date it was verified
> against the live service**, because a public court API changes without announcement and an
> undated claim cannot be falsified. Where something has not been verified, write that it has
> not been verified. Do not infer endpoint behaviour from documentation: three of the facts
> that shaped the first two connectors contradicted the published documentation and were
> found only by running against the live service (issues #7 and #8).

### 3.1 Endpoints

| Endpoint | Purpose | Verified |
| --- | --- | --- |
| `<url>` | \<what it returns\> | \<date\> |

> Annex 2a of the core document holds the one-line summary of each row; this section holds
> the detail. Keep the two consistent, and record here anything Annex 2a is too terse for.

### 3.2 Parameters, quirks and limits

> The parameters used, their accepted values, and every trap. A trap is worth a paragraph if
> getting it wrong costs documents rather than style. Give the observed evidence: the status
> code, the error body, the counts on each side of a choice.
>
> State result caps, page sizes, silent clamping, time zones, identifier encoding, and any
> parameter whose omission changes the *population* rather than the *format* of what comes
> back. A parameter that changes the population is a selection decision and belongs in §5 if
> it excludes anything.

### 3.3 Discovery, incrementality and what a run costs

> How the window is walked, what the checkpoint means, how a re-run avoids re-fetching, and
> the measured cost of a run: documents, requests, wall time, errors, retries, backoffs. A
> public court endpoint is a shared resource and the observed load belongs on the record.

### 3.4 What the source does not expose

> Fields the schema wants and the source does not have; classifications that exist but do not
> mean what the schema means by them; text that exists in one form only. Anything a later
> reclassification could not recover without going back to the court.

---

## 4. The keyword list

> Requirement 3 of §2.9. Not a restatement of `data/keywords/README.md` — that document
> explains the mechanism. **This section explains why *these* terms, in *this* jurisdiction.**

### 4.1 The file and its scoring

> Version, languages, term count, `min_score`, the field multipliers, and any way in which
> the scoring configuration differs from another jurisdiction's — differences are legitimate,
> undocumented differences are not.

### 4.2 Why these terms

> Work through the three axes §2.5 names, and be concrete:
>
> - **Language.** Which languages the courts work in, and what the language does to matching
>   — compounding, inflection, diacritics, homonyms. Name the homonyms and how each is
>   disarmed.
> - **Legal system.** The national statutes, the authorising body, the authorisation
>   procedure, the enforcement statute. These have no cross-jurisdiction equivalent and are
>   the highest-precision signals available.
> - **Agronomy.** The crops and practices that actually generate litigation in this country.

### 4.3 What the first dry run measured

> The evidence, plainly: documents in the window, how many passed, precision on a hand-read
> sample, and the score distribution. **Report the distribution, not just the headline
> precision** — under §2.7 the response to poor precision is the review queue, and the queue
> is only well-targeted if the false positives are concentrated in a band. Say whether they
> are.
>
> Recall is the number that matters and is usually not measurable without labelled ground
> truth. Say so, and record the best available substitute: which cases you expected to find
> and did, and what a sample immediately below the threshold contains.

---

## 5. Documented exceptions (§2.10)

> The selection method is the same in every jurisdiction: fetch, filter, rank, recall-first
> (§2.7, §2.10). Jurisdictions differ in their **inputs** — endpoints and terms — not in how
> selection works. Anything this jurisdiction genuinely needs beyond that lives here, and
> nowhere else. It never goes into shared code.
>
> **Every exception states what it excludes, why, and what it costs.** Because an exclusion
> is a deliberate false negative, the justification carries a higher burden than an inclusion:
> an inclusion that turns out to be wrong costs a reviewer a minute, an exclusion that turns
> out to be wrong is a case the tracker implicitly claims does not exist, and a researcher
> cannot audit an absence. An exception that could not be explained on the public Methodology
> page (§2.8) does not qualify.
>
> **Curation is the content manager's (§2.3).** This section presents evidence and the trade;
> it does not decide. Keep each entry's status accurate and link the issue where the decision
> is being taken.

### 5.1 Register

| # | Status | What it would exclude | Instrument | Issue |
| --- | --- | --- | --- | --- |
| 1 | *in force / candidate / declined* | \<one line\> | *exclusion / `requires` / match mode / alias change* | #\<n\> |

### 5.2 \<Short name of the exception\>

**Status.** *Candidate — not applied. The shipped list is unchanged.*

**What it would exclude.** \<Precisely which documents stop being selected.\>

**Evidence.** \<Measured, with the identifiers. A defect asserted from reading a list is a
hypothesis; a defect reproduced against real judgments is evidence. Give the score.\>

**Why.** \<Why the matched text is not pesticide litigation. Distinguish a *linguistic
accident* — a homonym, a compound, an acronym collision — from a genuine disagreement about
scope. Only the first is a candidate for an exception; the second is a question for §1.\>

**What it costs.** \<The false negatives it buys. Name the pesticide case that would be lost
if the excluded pattern also appears in genuine litigation, or state on what evidence you
believe none exists. Note the instrument's blast radius: a whole-document veto is far blunter
than a match-mode change, since it discards the document whatever else it says.\>

**Recall impact.** \<Under §2.7 this is the deciding number. "None observed in the run" is an
acceptable answer if the run is described; "probably none" is not.\>

**Who decides.** Content manager (§2.3), via #\<issue\>.

---

## 6. Known limitations

> Requirement 5 of §2.9. What this jurisdiction's data does not support, stated so that a
> researcher can cite the tracker without overclaiming. Include the source's own publication
> policy where it selects what is published, coverage start dates, any population the
> connector deliberately does not fetch (cross-reference §5), languages the list does not
> cover, and anything measured but unexplained.
>
> Distinguish clearly between *known to be absent*, *known to be incomplete by an unknown
> amount*, and *not investigated*. The third is honest; disguising it as the first is not.

---

## 7. Verification log

> One row per fact-finding pass against the live service. This is what turns §3 from an
> assertion into a claim with a date on it, and what a future reader checks before trusting
> any of it.

| Date | What was verified | By | Result |
| --- | --- | --- | --- |
| \<date\> | \<endpoint or behaviour\> | \<role, issue or PR\> | \<what was found\> |

---

## 8. Sources

> Where each class of fact in this document came from: the core document and its annexes, the
> connector and its docstrings, the keyword list, the dry-run reports on specific issues, and
> any primary legal source used in §2. A reader who disagrees with a claim should be able to
> reach its origin in one step.
