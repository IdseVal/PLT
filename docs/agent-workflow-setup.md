# Setting up the agent workflow on a new project

Everything needed to stand this up from an empty repository, in order, with the commands to
run and the reasons behind the choices that are not obvious.

Written to be project-agnostic. Where something is specific to the project it lives in, it is
marked **[project-specific]** and says what to substitute.

Verified on 22 August 2026 against the live tooling. Package names, CLI verbs and skill names
were each read back from the tool rather than copied from a plan — four things in the original
design turned out not to exist, and the section [What this corrects](#what-this-corrects)
records them so nobody rediscovers them the hard way.

---

## 1. What you are building

```
HOST                                    CONTAINER
  Orca (desktop application)     ──▶      repo + pinned toolchain + skills
  dispatches agents                       tests, builds, project commands
  owns the git worktrees                  large read-only data mounted in
```

**Orca runs on the host, not in the container.** It is a desktop application, not an npm
package, and it cannot live inside the environment it dispatches into. The container is the
reproducible place where agents *execute*: a pinned toolchain, so a test that passes for one
agent passes for every agent and later on the deployment target.

Work flows: **issue → worktree → developer → tester → reviewer → `dev`**. A person, and only a
person, promotes `dev` to `main`.

### Isolation: be honest about which one you want

Two different things get called isolation, and conflating them is how a setup looks safe
without being safe.

| You want | Do this | Cost |
| --- | --- | --- |
| **Reproducibility** — same toolchain everywhere, and a path to deployment | A plain dev container. No `docker.sock`. | None. This is the default here. |
| **Containment** — the agent genuinely cannot reach the host | Rootless Docker, or sysbox, or a throwaway VM | Real setup work |

**Do not mount `/var/run/docker.sock` and call the result isolated.** It hands anything inside
the container root-equivalent control of the host daemon — enough to start a privileged
container that mounts the host filesystem. Mount it only if you need container-in-container
testing, and write down that you have made that trade.

---

## 2. Prerequisites

| Tool | Why | Check |
| --- | --- | --- |
| Docker Desktop | Builds and runs the dev container | `docker info` |
| VS Code + Dev Containers extension | Opens the repo inside it | — |
| Orca | Dispatches the agents | `orca --version` |
| GitHub CLI | The digest reads merged pull requests | `gh auth status` |
| Node ≥ 22.20 | The `skills` CLI refuses to run below this | `node --version` |

---

## 3. Directory layout

```
.devcontainer/
  Dockerfile              pinned toolchain
  devcontainer.json       mounts, environment, post-create hook
  post-create.sh          dependencies + skills, on first open
.orca/
  dispatch.yml            labels → roles → skills; pipelines; circuit breaker
  setup_skills.sh         fetches every skill
  system_prompts/         one file per role
docs/
  specs/CORE_SPEC.md      what the project is for
  adrs/ADR-NNN-*.md       one decision per file
mail/
  daily_digest.py         the digest, derived from git and pull requests
.github/workflows/
  daily-digest.yml        sends it, and only when work landed
```

Create it:

```bash
mkdir -p .devcontainer .orca/system_prompts docs/adrs docs/specs mail .github/workflows
```

---

## 4. The container

Copy `.devcontainer/` from this repository and change three things.

**The base image.** `mcr.microsoft.com/devcontainers/typescript-node:1-22-bookworm` suits a
Node + Python project. Node **22**, not 20 — the `skills` CLI requires ≥ 22.20.0 and refuses
to start below it. Bookworm carries Python 3.11.

**The Python virtualenv lives outside the workspace.** `/opt/venv`, never `/workspace/.venv`.
A bind-mounted workspace shared with a Windows or macOS host would otherwise have the
container's Linux virtualenv overwrite the host's, leaving neither working. This is the single
most common way a dev container breaks a working checkout.

**Large data is mounted, not copied, and mounted read-only.** **[project-specific]** — here
that is the case-law corpus:

```jsonc
"mounts": [
  "source=${localEnv:PLT_CORPUS_HOST_DIR},target=/corpus,type=bind,readonly"
],
"remoteEnv": { "PLT_CORPUS_STORE_DIR": "/corpus" }
```

Read-only because a container that can delete it can destroy something that takes days of
polite requests to rebuild. Read the host path from `localEnv` so no machine-specific path is
committed; set it before opening the container.

Then:

```bash
export PLT_CORPUS_HOST_DIR=/your/corpus     # [project-specific]
code .                                       # → "Reopen in Container"
```

First open builds the image and runs `post-create.sh`. Confirm:

```bash
python --version && node --version
npx skills list
```

---

## 5. Skills

The CLI is [`skills`](https://github.com/vercel-labs/skills) from vercel-labs.

**Its verb is `add`, not `install`, and there is no `--target`.** Scope is project by default,
or `--global`. Skills land in the agent directories `--agent` selects.

```bash
npx -y skills@latest add <owner>/<repo> --skill <skill-name> --agent claude --yes
```

**Read the skill names back before you commit them.** A wrong name fails the container build
for everyone, and the names in a plan are frequently wrong:

```bash
npx -y skills@latest add mattpocock/skills --list
```

### The matrix

| Role | Skills | Repository |
| --- | --- | --- |
| Every role | `handoff` | `mattpocock/skills` |
| PO & Analyst | `grill-with-docs`, `domain-modeling` | `mattpocock/skills` |
| Architect | `improve-codebase-architecture`, `codebase-design` | `mattpocock/skills` |
| Researcher | `just-scrape` | `scrapegraphai/just-scrape` |
| Developer (`ui`) | `frontend-design` | `anthropics/skills` |
| Developer (`ui`) | `web-design-guidelines` | `vercel-labs/agent-skills` |
| Developer (`ui`) | `high-end-visual-design` | `leonxlnx/taste-skill` |
| Developer (`seo`) | `seo-audit` | `coreyhaines31/marketingskills` |
| Developer (`scraper`) | `just-scrape` | `scrapegraphai/just-scrape` |
| Developer (`bug`) | `diagnosing-bugs` | `mattpocock/skills` |
| Tester | `tdd` | `mattpocock/skills` |
| Tester | `webapp-testing` | `anthropics/skills` |
| Reviewer | `code-review` | `mattpocock/skills` |

All verified present on 22 August 2026.

---

## 6. Roles and routing

Copy `.orca/system_prompts/` and `.orca/dispatch.yml`. Each role file is short and says what
that role does, what it must not do, and what it owes the next role.

Three rules are load-bearing and belong in every project:

1. **`main` is off limits to every agent.** Never open, prepare or merge a pull request into
   it. Promotion is the owner's, after they have tested `dev` themselves.
2. **Skills follow labels.** `ui` earns the design stack, `bug` earns the diagnosis skill.
   Loading everything for every issue makes small work too expensive to do, which is how small
   work stops getting done.
3. **The `trivial` label skips the tester, never the reviewer.** For a typo or a version bump
   a test author has nothing to write. The reviewer still reads every line, and *the reviewer
   refuses the label* when the change turns out to touch behaviour. A label is a request, not
   a permission.

---

## 7. The circuit breaker, and what it does not catch

Three round trips between developer, tester and reviewer. On the fourth: freeze the worktree,
stop automation, hand a person the diff and the failing output.

**A bounce counter catches thrashing. It does not catch the failure that actually costs you**,
which is a run that reports success while being wrong. Two from this project's own history:

- a corpus walk reported `success, 0 failures` while silently dropping **14.9%** of what it
  was asked for;
- a keyword filter passed every test while selecting **41 judgments on a Luxembourg street
  name**, because a six-character substring sat inside it.

Neither bounced. Both passed. So the reviewer may not merge on green tests alone — the
`evidence_gates` in `dispatch.yml` require a claim checked against something the change did
not produce:

- **counts reconciled** — a number from the run against a number from the source or the store,
  both quoted in the pull request. "It completed" is not a count.
- **output sampled** — enough real rows pasted in that a wrong one would be visible.
- **cost measured** — against its budget, where one is documented.

This is the same rule good pipelines already follow: *an artefact describes itself by
observation, not by declaration.*

---

## 8. The daily digest

`mail/daily_digest.py` builds it from **commit and pull-request history**, not from logs the
agents write.

That choice matters. Agent-written logs get forgotten; two agents in parallel worktrees
appending to one daily file conflict on every merge; and a report assembled from
self-description tells you what each agent *believed*, which is exactly the thing you are
trying to check. Commits and pull requests exist anyway and cannot be skipped.

```bash
python mail/daily_digest.py --branch dev --print              # today, printed
python mail/daily_digest.py --branch dev --day 2026-08-17 --print
```

With no SMTP credentials it prints instead of sending, so it is always safe to run.

**It sends nothing on a quiet day.** A scheduled mail that arrives empty is one people learn
to skim past.

Set these repository secrets to enable sending: `SMTP_SENDER_EMAIL`, `SMTP_RECEIVER_EMAIL`,
`SMTP_PASSWORD`, and optionally `SMTP_SERVER` and `SMTP_PORT`. Until they are set the
scheduled run renders into the workflow log, which is a good way to check the content is worth
reading before it starts arriving in your inbox.

---

## 9. Running it

**A — onboarding.** Ask the dispatcher to start onboarding. The PO and Analyst interviews you
with `grill-with-docs`, then writes `docs/specs/CORE_SPEC.md` and the first ADRs.

> If the project already has a specification, **port it rather than re-interviewing**. An
> interview that rediscovers decided things wastes the one resource an agent setup cannot
> manufacture, which is your attention.

**B — contracts.** The Architect freezes interfaces and schemas on `dev` before feature work
opens. A contract that changes while three worktrees are open against it is not a contract.

**C — issues.** The PO writes them with labels: `role:*`, plus `ui` / `seo` / `scraper` /
`bug` / `data`, plus `trivial` where it genuinely applies.

**D — dispatch.** New issue → worktree `feature/issue-<n>` off `dev` → developer with the
labelled skills.

**E — test.** For large components the tester runs a Failure Mode and Effects Analysis
interview with you and gets sign-off before writing anything. For ordinary logic it writes
tests directly.

**F — review.** Standards, spec compliance, evidence gate. Then merge to `dev` and delete the
worktree.

**G — promotion.** You inspect `dev` and open the pull request to `main` yourself. No agent
ever does.

---

## 10. What this corrects

Four things in the original design did not exist as written. Each was found by checking before
building, which cost an hour and saved a container nobody could open.

| Claim | Reality |
| --- | --- |
| `npm install -g @orca-ade/cli` | No such package. `npm view` returns 404, and the image build fails on it. |
| Orca installs into the container | It is a desktop application on the host. It cannot be containerised. |
| `npx skills install <repo>/<skill> --target <dir>` | The verb is `add`; the flag is `--skill`; there is no `--target`. |
| Base image `typescript-node:1-20-bullseye` | `skills` requires Node ≥ 22.20.0. Node 20 fails with `EBADENGINE`. |
| Skill `diagnose` | It is called `diagnosing-bugs`. |

The general lesson is the one the evidence gates encode: **check the thing, do not trust the
description of the thing** — including this document. If a command here fails, the tool has
moved and the document is wrong.

---

## 11. Checklist

```
[ ] docker info succeeds
[ ] Reopen in Container succeeds; python --version and node --version answer
[ ] npx skills list shows every skill in the matrix
[ ] Large data mounts read-only, and the project's own test suite passes in the container
[ ] .orca/dispatch.yml names your labels and pipelines
[ ] Every role prompt forbids merging to main
[ ] python mail/daily_digest.py --print renders a real day
[ ] Branch protection on main: no direct pushes, pull request required
[ ] SMTP secrets set (or deliberately left unset, so the digest prints to the log)
```
