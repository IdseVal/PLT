# The ORCA ADE agent workflow: complete setup

How to build this environment from an empty repository. Every file is listed in full, so the
whole workflow is constructable from this document alone.

Project-agnostic. Substitute your own names wherever a placeholder appears; nothing here is
tied to a particular codebase.

---

## Contents

1. [What you are building](#1-what-you-are-building)
2. [Prerequisites](#2-prerequisites)
3. [Repository layout](#3-repository-layout)
4. [Step 1 — The core document](#4-step-1--the-core-document)
5. [Step 2 — The container](#5-step-2--the-container)
6. [Step 3 — Skills](#6-step-3--skills)
7. [Step 4 — The agents](#7-step-4--the-agents)
8. [Step 5 — Routing](#8-step-5--routing)
9. [Step 6 — Running the workflow, A to G](#9-step-6--running-the-workflow-a-to-g)
10. [The circuit breaker](#10-the-circuit-breaker)
11. [The FMEA protocol](#11-the-fmea-protocol)
12. [Evidence before merge](#12-evidence-before-merge)
13. [The daily digest](#13-the-daily-digest)
14. [Branch protection](#14-branch-protection)
15. [Known tooling pitfalls](#15-known-tooling-pitfalls)
16. [Checklist](#16-checklist)

---

## 1. What you are building

```
                        ┌───────────────────────────┐
                        │      HUMAN GATEKEEPER     │
                        │  the only one who may     │
                        │  merge  dev  ->  main     │
                        └─────────────┬─────────────┘
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │      DISPATCHER AGENT     │
                        │  worktrees · skill        │
                        │  routing · circuit breaker│
                        └─────────────┬─────────────┘
                                      │
      ┌──────────────┬────────────────┼────────────────┬──────────────┐
      ▼              ▼                ▼                ▼              ▼
┌───────────┐ ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐
│    PO &   │ │ Architect │   │Researcher │   │ Developer │   │  Testing  │
│  Analyst  │ │           │   │           │   │           │   │           │
└───────────┘ └───────────┘   └───────────┘   └───────────┘   └─────┬─────┘
                                                                    │
                                                              ┌─────▼─────┐
                                                              │ Reviewer  │
                                                              │ merges to │
                                                              │    dev    │
                                                              └───────────┘
```

**Where each part runs:**

```
HOST                                    CONTAINER
  Orca (desktop application)     ──▶      repo + pinned toolchain + skills
  dispatches agents                       tests, builds, project commands
  owns the git worktrees                  large read-only data mounted in
```

Orca runs on the host. It is a desktop application, not an npm package, and it cannot live
inside the environment it dispatches into. The container is the reproducible place where
agents *execute*: a pinned toolchain, so a test that passes for one agent passes for every
agent and later on the deployment target.

### Isolation: decide which one you want

Two different things get called isolation, and conflating them is how a setup looks safe
without being safe.

| You want | Do this | Cost |
| --- | --- | --- |
| **Reproducibility** — same toolchain everywhere, and a path to deployment | A plain dev container. No `docker.sock`. | None |
| **Containment** — the agent genuinely cannot reach the host | Rootless Docker, sysbox, or a throwaway VM | Real setup work |

Mounting `/var/run/docker.sock` gives anything inside the container root-equivalent control of
the host daemon — enough to start a privileged container that mounts the host filesystem. It
is not isolation. Mount it only if you need container-in-container testing, and record that
you made the trade.

---

## 2. Prerequisites

| Tool | Why | Check |
| --- | --- | --- |
| Docker Desktop | Builds and runs the dev container | `docker info` |
| VS Code + Dev Containers extension | Opens the repo inside it | — |
| Orca | Dispatches agents, owns worktrees | `orca --version` |
| GitHub CLI, authenticated | The digest reads merged pull requests | `gh auth status` |
| Node ≥ 22.20 on the host | The `skills` CLI floor | `node --version` |

---

## 3. Repository layout

```
.devcontainer/
  Dockerfile              pinned toolchain
  devcontainer.json       mounts, environment, post-create hook
  post-create.sh          dependencies + skills, on first open
.orca/
  dispatch.yml            labels -> roles -> skills; pipelines; circuit breaker
  setup_skills.sh         fetches every skill
  system_prompts/
    dispatcher.md  po-analyst.md  architect.md  researcher.md
    developer.md   tester.md      reviewer.md
docs/
  CORE_DOCUMENT.md        the single source of truth; everything derives from it
  specs/                  derived specifications
  adrs/                   one decision per file
mail/
  daily_digest.py         digest, derived from git and pull requests
.github/workflows/
  daily-digest.yml        sends it, and only when work landed
.gitattributes            *.sh pinned to LF
```

Create it:

```bash
mkdir -p .devcontainer .orca/system_prompts docs/adrs docs/specs mail .github/workflows
```

---

## 4. Step 1 — The core document

**Every project starts from a core document, and nothing else exists until it does.** No
architecture, no issues, no code. It is the single source of truth for what the project is,
and every later artefact — specs, ADRs, issues, tests — is derived from it.

The PO & Analyst Agent owns it and populates it by **deep interview with the human**. It is
never inferred, never filled with a plausible default, and never written from the agent's own
idea of what the project probably wants.

Create the file before you start:

```bash
cat > docs/CORE_DOCUMENT.md <<'EOF'
# Core document

> Populated by deep interview with the project owner. Nothing here is inferred.
> Status: EMPTY — run the onboarding interview.

## 1. Purpose and success criteria
## 2. Target users
## 3. Scope
## 4. Explicit NON-scope
## 5. Domain model and vocabulary
## 6. Data sources and their constraints
## 7. External systems
## 8. Legal, privacy and compliance limits
## 9. What must never happen
## 10. Open questions
EOF
```

The interview protocol is in the PO & Analyst system prompt in
[section 7](#7-step-4--the-agents). Two rules govern it:

- **Interview in rounds.** Ask, record the answer, read it back, ask what is now wrong or
  missing. A single pass produces a document the owner does not recognise.
- **An unknown is recorded as OPEN, never guessed.** An open question written down is a
  decision waiting. A guess written down is a defect shipped, and every later artefact
  inherits it.

The core document is living. When a decision changes it, it is updated and the change is
stated. A spec may never silently contradict it.

---

## 5. Step 2 — The container

### `.devcontainer/Dockerfile`

```dockerfile
# The reproducible toolchain every agent runs inside.
#
# It pins the toolchain so a test that passes here passes on any machine, and it is the shape
# the project deploys in later. It is NOT a sandbox: the container is not a security boundary.
#
# Orca is not installed. It is a desktop application on the host that dispatches agents and
# owns the worktrees; it cannot live inside the environment it dispatches into, and it is not
# published to npm at all.

FROM mcr.microsoft.com/devcontainers/typescript-node:1-22-bookworm

# git and gh: the daily digest is derived from commit and pull-request history, so the
# container reads both. jq keeps shell-side JSON handling out of Python.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        gh \
        jq \
        curl \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# The `1-22` tag ships Node 22.16, BELOW the 22.20.0 floor the `skills` CLI declares. npm runs
# it anyway with an EBADENGINE warning; a toolchain that works by warning breaks the day npm
# enforces it. `n` pins the interpreter above the floor whatever the base image moves to.
#
# Nothing is chained onto this step. `n` replaces /usr/local/bin/node underneath the running
# shell, so a subsequent npm in the same RUN resolves to the binary just swapped out.
RUN npm install -g n && n 22.20.0

# Debian marks the system interpreter externally managed, so a virtualenv is the supported way
# to install into it. Put it OUTSIDE the workspace: a bind-mounted workspace shared with a
# Windows or macOS host would otherwise have the container's Linux venv overwrite the host's,
# leaving neither working.
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /workspace
```

### `.devcontainer/devcontainer.json`

Substitute `PROJECT_DATA_HOST_DIR` and the target path for your project's own large dataset,
or delete the `mounts` block entirely if it has none.

```jsonc
{
  "name": "Agent workspace",

  "build": { "dockerfile": "Dockerfile" },
  "workspaceFolder": "/workspace",

  // Large datasets are mounted, not copied, and mounted READ-ONLY: a container that can
  // delete the data can destroy something expensive or impossible to rebuild. The host path
  // is read from the environment so no machine-specific path is committed. Leave the variable
  // unset and the container still opens; only the commands needing that data are unavailable.
  "mounts": [
    "source=${localEnv:PROJECT_DATA_HOST_DIR},target=/data,type=bind,readonly"
  ],

  // No docker.sock. See "Isolation" above.

  "remoteEnv": {
    "PROJECT_DATA_DIR": "/data"
  },

  "postCreateCommand": "bash .devcontainer/post-create.sh",

  "customizations": {
    "vscode": {
      "extensions": [
        "ms-python.python",
        "charliermarsh.ruff",
        "dbaeumer.vscode-eslint",
        "esbenp.prettier-vscode"
      ],
      "settings": {
        "python.defaultInterpreterPath": "/opt/venv/bin/python"
      }
    }
  }
}
```

### `.devcontainer/post-create.sh`

Adapt the two dependency lines to your project's own layout.

```bash
#!/usr/bin/env bash
#
# Everything the container needs that the image cannot bake in: project dependencies, which
# change with the lockfiles, and the agent skills, which are fetched from GitHub.
#
# Deliberately NOT `set -e` around the whole file. A failed skill fetch is a network problem
# and must not leave a developer with no container at all; a failed dependency install must.
set -uo pipefail

cd /workspace

echo "==> Python dependencies"
set -e
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e "backend[dev]" -c backend/constraints.txt
set +e

echo "==> Node dependencies"
if [ -f frontend/package-lock.json ]; then
  (cd frontend && npm ci --no-audit --no-fund) || {
    echo "!! npm ci failed. The container is usable; run it by hand." >&2
  }
fi

echo "==> Agent skills"
if bash .orca/setup_skills.sh; then
  echo "    skills installed"
else
  echo "!! Skill installation failed — usually network or a GitHub rate limit." >&2
  echo "   Re-run: bash .orca/setup_skills.sh" >&2
fi
```

### `.gitattributes`

Required if anyone works on Windows:

```gitattributes
* text=auto eol=lf

# Executed inside the Linux dev container, which bind-mounts the working tree rather than a
# fresh checkout. A CRLF here is invisible on Windows and fails in the container with
# `set: pipefail: invalid option name`, which points nowhere near the real cause.
*.sh text eol=lf
```

### Open it

```bash
export PROJECT_DATA_HOST_DIR=/path/to/your/data    # omit if you have none
code .                                              # -> "Reopen in Container"
```

Confirm inside:

```bash
python --version && node --version    # node must be >= 22.20.0
npx skills list
```

---

## 6. Step 3 — Skills

The CLI is [`skills`](https://github.com/vercel-labs/skills) (npm package: `skills`).

**Three facts that differ from most written instructions:**

- The verb is **`add`**, not `install`.
- There is **no `--target`**. Scope is project by default, or `--global`.
- The agent id is **`claude-code`**. Plain `claude` is rejected as invalid, which fails every
  install at once.

```bash
npx -y skills@latest add <owner>/<repo> --skill <skill-name> --agent claude-code --yes
```

**Read skill names back from the repository before committing them.** Names in a plan are
frequently wrong, and a wrong name fails the container build for everyone:

```bash
npx -y skills@latest add <owner>/<repo> --list
```

### The skill matrix

| Agent | Skills | Repository |
| --- | --- | --- |
| Every agent | `handoff` | `mattpocock/skills` |
| PO & Analyst | `grill-with-docs`, `domain-modeling` | `mattpocock/skills` |
| Architect | `improve-codebase-architecture`, `codebase-design` | `mattpocock/skills` |
| Researcher | `just-scrape` | `scrapegraphai/just-scrape` |
| Researcher | `diagnosing-bugs` | `mattpocock/skills` |
| Developer — label `ui` | `frontend-design` | `anthropics/skills` |
| Developer — label `ui` | `web-design-guidelines` | `vercel-labs/agent-skills` |
| Developer — label `ui` | `high-end-visual-design` | `leonxlnx/taste-skill` |
| Developer — label `seo` | `seo-audit` | `coreyhaines31/marketingskills` |
| Developer — label `scraper` | `just-scrape` | `scrapegraphai/just-scrape` |
| Developer — label `bug` | `diagnosing-bugs` | `mattpocock/skills` |
| Testing | `tdd` | `mattpocock/skills` |
| Testing | `webapp-testing` | `anthropics/skills` |
| Reviewer | `code-review` | `mattpocock/skills` |

### `.orca/setup_skills.sh`

```bash
#!/usr/bin/env bash
#
# Fetch every agent skill this workspace dispatches, into the project scope.
#
# The CLI verb is `add`, not install. There is no --target. The agent id is `claude-code`.
# Node >= 22.20.0 is required; the Dockerfile pins it upward with `n`.
#
# Every skill name here was read back from the repository with `--list` rather than copied
# from a plan. A name that is wrong fails the container build for everyone.
set -uo pipefail

SKILLS_CLI="${SKILLS_CLI:-npx -y skills@latest}"
AGENT="${SKILLS_AGENT:-claude-code}"

failed=()

# add <owner/repo> <skill> [<skill>...]
add() {
  local repo="$1"; shift
  local skill
  for skill in "$@"; do
    printf '  %-34s %s\n' "$skill" "($repo)"
    # shellcheck disable=SC2086
    if ! $SKILLS_CLI add "$repo" --skill "$skill" --agent "$AGENT" --yes >/dev/null 2>&1; then
      failed+=("$repo@$skill")
    fi
  done
}

echo "Installing agent skills (agent: $AGENT)"

add mattpocock/skills handoff
add mattpocock/skills grill-with-docs domain-modeling
add mattpocock/skills improve-codebase-architecture codebase-design
add anthropics/skills frontend-design
add vercel-labs/agent-skills web-design-guidelines
add leonxlnx/taste-skill high-end-visual-design
add coreyhaines31/marketingskills seo-audit
add scrapegraphai/just-scrape just-scrape
add mattpocock/skills diagnosing-bugs
add mattpocock/skills tdd
add anthropics/skills webapp-testing
add mattpocock/skills code-review

echo
if [ ${#failed[@]} -eq 0 ]; then
  echo "All skills installed."
else
  echo "Failed (${#failed[@]}):" >&2
  printf '  %s\n' "${failed[@]}" >&2
  echo "Re-run this script; GitHub rate limits are the usual cause." >&2
  exit 1
fi
```

```bash
chmod +x .orca/setup_skills.sh .devcontainer/post-create.sh
```

---

## 7. Step 4 — The agents

Seven agents. Each block below is a complete system prompt; write it to the path given.

| Agent | File | Owns | May merge |
| --- | --- | --- | --- |
| Dispatcher | `.orca/system_prompts/dispatcher.md` | Worktrees, routing, circuit breaker | nothing |
| PO & Analyst | `.orca/system_prompts/po-analyst.md` | `docs/CORE_DOCUMENT.md`, specs, ADRs, issues | nothing |
| Architect | `.orca/system_prompts/architect.md` | Contracts, schemas, module boundaries | nothing |
| Researcher | `.orca/system_prompts/researcher.md` | Facts about external systems, bug localisation | nothing |
| Developer | `.orca/system_prompts/developer.md` | Implementation inside one worktree | nothing |
| Testing | `.orca/system_prompts/tester.md` | FMEA, test suites, test execution | nothing |
| Reviewer | `.orca/system_prompts/reviewer.md` | Standards, spec compliance | **`dev` only** |

Nobody merges to `main`. That is the human's, always.

### A. Dispatcher Agent — `.orca/system_prompts/dispatcher.md`

```markdown
You are the Dispatcher Agent in an ORCA ADE setup.
RESPONSIBILITIES:
1. Parse incoming GitHub Issue webhooks and user commands.
2. Spin up isolated Orca Git worktrees off branch `dev` formatted as `feature/issue-<id>`.
3. Route issues to the designated agent label.

CIRCUIT BREAKER SAFETY RULES:
- Track the cycle count for every issue (Developer <-> Tester/Reviewer loops).
- IF an issue cycles more than 3 TIMES without passing tests/review:
  1. HIDE/HALT the worktree execution.
  2. Post an escalation summary on the GitHub Issue: "Circuit breaker tripped: 3 failed attempts."
  3. Reassign the issue directly to @human with a diff patch and error logs.
  4. DO NOT loop back to the Developer Agent.
```

### B. PO & Analyst Agent — `.orca/system_prompts/po-analyst.md`

```markdown
You are the PO & Analyst Agent.
RESPONSIBILITIES:
1. Own `/docs/CORE_DOCUMENT.md`. Every project starts from it and every later artefact is
   derived from it. It is the single source of truth for what this project is.
2. Populate it by DEEP INTERVIEW with the human. Never infer, never assume, never fill a gap
   with a plausible default.
3. Derive `/docs/specs/*.md` and `/docs/adrs/ADR-NNN-*.md` from the core document once it is
   agreed.
4. Write GitHub Issues carrying role and skill labels.

INTERVIEW PROTOCOL:
- The core document comes FIRST. No architecture, no issues and no code exist until it is
  agreed with the human.
- Interview in rounds. Each round: ask, record the answer in the core document, read it back,
  ask what is now wrong or missing.
- Interrogate rather than collect. The first answer is usually the answer to a question you
  did not ask. Push until a boundary is sharp enough that a developer could not build the
  wrong thing without noticing.
- Cover at minimum: purpose and success criteria; target users; scope and explicit
  NON-scope; the domain model and its vocabulary; data sources and their constraints;
  external systems; legal, privacy and compliance limits; what must never happen.
- Where the human does not know, record it as OPEN with their name against it. An open
  question written down is a decision waiting; a guess written down is a defect shipped.
- Use `grill-with-docs` for the interview and `domain-modeling` to fix the vocabulary. One
  name per concept, written down, used everywhere afterwards.

OUTPUT RULES:
- `/docs/CORE_DOCUMENT.md` is living. When a decision changes it, update it and say what
  changed; never let a spec contradict it silently.
- An ADR records ONE decision, with the alternatives rejected and why. A decision without its
  discarded options is an assertion, not a record.
- An Issue states what is wanted, how anyone will know it worked, and what is out of scope.

FORBIDDEN:
- You do not write implementation code.
- You are STRICTLY FORBIDDEN from merging any branch into `main`.
```

### C. Architect Agent — `.orca/system_prompts/architect.md`

```markdown
You are the Architect Agent.
RESPONSIBILITIES:
1. Read `/docs/CORE_DOCUMENT.md` and the specs derived from it.
2. Produce and FREEZE the system contracts on `dev` before any feature work opens: interface
   types, schemas, API shapes, module boundaries.
3. Record each boundary decision as an ADR in `/docs/adrs/`.
4. Define the repository layout and the seams new work must fit into.

DESIGN RULES:
- Prefer a deep module with a narrow interface to a shallow one with a wide interface. A seam
  that exposes as much as it hides has cost a name and bought nothing.
- Contracts are frozen before feature worktrees open against them. A contract that changes
  while three worktrees depend on it is not a contract.
- If a frozen contract turns out to be wrong, STOP the work that depends on it, say so, and
  change it deliberately. Never let it drift.
- Never widen an interface to unblock one caller. That is how an interface becomes the union
  of every caller's convenience.
- Use `improve-codebase-architecture` and `codebase-design`.

FORBIDDEN:
- You do not implement features.
- You are STRICTLY FORBIDDEN from merging any branch into `main`.
```

### D. Researcher Agent — `.orca/system_prompts/researcher.md`

```markdown
You are the Researcher Agent.
RESPONSIBILITIES:
1. Answer questions of fact so that no other agent has to guess.
2. Establish what an external source, endpoint or page ACTUALLY returns, using `just-scrape`.
3. Reproduce and localise failures with `diagnosing-bugs` before anyone proposes a fix.
4. Write findings where the agent that needs them will find them, and state how you know.

EVIDENCE RULES:
- MEASURE, DO NOT INFER. A claim about an external system is worth exactly what its
  observation is worth. "The documentation says" is not an observation.
- Quote the request you sent and the response you got. A finding without its evidence is a
  rumour with a citation.
- Label a hypothesis as a hypothesis. If you have not seen it, you do not know it.
- Report an honest UNKNOWN rather than a confident guess. The next agent builds on what you
  write, and a wrong certainty propagates further than a stated gap.

FORBIDDEN:
- You do not change production code.
- You are STRICTLY FORBIDDEN from merging any branch into `main`.
```

### E. Developer Agent — `.orca/system_prompts/developer.md`

```markdown
You are the Developer Agent.
RESPONSIBILITIES:
1. Implement ONE issue inside the Orca worktree assigned to you, `feature/issue-<id>`.
2. Work only inside the dev container.
3. Read `/docs/CORE_DOCUMENT.md`, the relevant spec and the ADRs before writing anything.
4. Hand the worktree to the Testing Agent when the issue is implemented.

CODE STANDARD:
- Easily readable, minimalist code. No unnecessary abstraction, no premature generalisation,
  no design pattern the problem did not ask for. The Reviewer will reject all three.
- Match the surrounding code: its naming, its idioms, its comment density.
- Comment WHY, never what. Complete docstrings on every public function, class and module.
- The smallest change that satisfies the issue. Anything more is scope you were not given.

SKILL RULES:
- The Dispatcher loads your skills from the issue labels (`ui`, `seo`, `scraper`, `bug`,
  `data`).
- IF the work needs a skill you were not given: STOP and say so. Do not improvise around a
  missing skill — the label is wrong and a human must fix it.

REPORTING RULES:
- State what you changed, why, and WHAT YOU MEASURED. For anything touching data or a
  pipeline that means a number, not an adjective.
- If you found a defect you did not fix, write it down. A known defect recorded is a
  decision; one left unsaid is a trap for the next agent.
- If the issue contradicts the core document or a spec, that is a FINDING. Report it; do not
  route around it.

FORBIDDEN:
- Never merge your own work. The Reviewer merges into `dev`.
- You are STRICTLY FORBIDDEN from merging any branch into `main`.
```

### F. Testing Agent — `.orca/system_prompts/tester.md`

```markdown
You are the Testing Agent.
RESPONSIBILITIES:
1. Validate all code inside the assigned Orca worktree.
2. Execute tests strictly inside isolated sandbox containers.

HYBRID TESTING STRATEGY:
- STANDARD CODE (Pure functions, utilities, parsing): Write automated tests directly based on specs.
- LARGE COMPONENTS (Data Fetching, Storage, Core Frontend):
  1. STOP before writing test suites.
  2. Generate a FMEA (Failure Mode and Effects Analysis) Markdown table covering:
     | Proposed Component | Failure Mode | Default Recovery Action | Human Confirmation Needed? |
  3. INTERVIEW THE HUMAN: Present this table to the user in chat. Ask for missing edge cases or custom failure actions.
  4. Once confirmed by the user, write the full test suite.
```

### G. Reviewer Agent — `.orca/system_prompts/reviewer.md`

```markdown
You are the Reviewer Agent.
RESPONSIBILITIES:
1. Review PRs merging feature worktrees into `dev`.
2. Ensure code strictly fulfills the specification in `/docs/specs/`.
3. Enforce Code Standard: "Easily readable, minimalist code." Reject unnecessary abstractions, premature generalizations, or overly complex design patterns.
4. Verify all automated tests pass.

MERGE CONTROL:
- You are PERMITTED to merge approved worktrees into `dev`.
- You are STRICTLY FORBIDDEN from merging any branch into `main`.
```

---

## 8. Step 5 — Routing

### `.orca/dispatch.yml`

```yaml
version: 1

branches:
  base: dev
  protected: [main]          # no agent may merge into these
  worktree_prefix: feature/

roles:
  po-analyst:
    prompt: .orca/system_prompts/po-analyst.md
    skills: [handoff, grill-with-docs, domain-modeling]
  architect:
    prompt: .orca/system_prompts/architect.md
    skills: [handoff, improve-codebase-architecture, codebase-design]
  researcher:
    prompt: .orca/system_prompts/researcher.md
    skills: [handoff, just-scrape, diagnosing-bugs]
  developer:
    prompt: .orca/system_prompts/developer.md
    skills: [handoff]        # baseline; labels add to it
  tester:
    prompt: .orca/system_prompts/tester.md
    skills: [handoff, tdd, webapp-testing]
  reviewer:
    prompt: .orca/system_prompts/reviewer.md
    skills: [handoff, code-review]

# A label is a claim about the work. It earns the skills that work needs and no others:
# loading the whole design stack for a typo is how small work becomes too expensive to do.
labels:
  ui:
    skills: [frontend-design, web-design-guidelines, high-end-visual-design]
  seo:
    skills: [seo-audit]
  scraper:
    skills: [just-scrape]
  bug:
    skills: [diagnosing-bugs]
  data:
    skills: [codebase-design]

pipelines:
  default:
    stages: [developer, tester, reviewer]

  # `trivial` skips the TESTER, never the reviewer. For a typo, a comment, a version bump or
  # a doc edit a test author has nothing to write, and every line is still read before it
  # lands. The REVIEWER refuses the label when the change turns out to touch behaviour: it is
  # a request, not a permission.
  trivial:
    stages: [developer, reviewer]
    forbid_labels: [ui, scraper, data]

circuit_breaker:
  max_cycles: 3
  on_trip:
    freeze_worktree: true
    halt_automation: true
    assign_to: human
    attach: [diff, test_output, agent_transcript_tail]

evidence_gates:
  - id: counts-reconciled
    applies_to: [data, pipeline]
    requires: >-
      A count from the run reconciled against a count from an independent source, both quoted
      in the pull request. "It completed" is not a count.
  - id: output-sampled
    applies_to: [data, ui]
    requires: >-
      A sample of the actual output pasted into the pull request. Enough rows that a wrong
      one would be visible.
  - id: cost-measured
    applies_to: [pipeline]
    requires: >-
      The measured cost of the change against its budget, where one is documented.
```

### Create the labels

```bash
gh label create ui      --description "Interface work; loads the design skills"
gh label create seo     --description "Search work; loads seo-audit"
gh label create scraper --description "External data acquisition; loads just-scrape"
gh label create bug     --description "Defect; loads diagnosing-bugs"
gh label create data    --description "Data or pipeline; evidence gate applies"
gh label create trivial --description "Skips the tester, never the reviewer"
```

---

## 9. Step 6 — Running the workflow, A to G

### A — Initialise

```bash
orca init --base-branch dev
orca --version && ls .orca/skills
```

### B — Onboarding interview

Send the Dispatcher: `@dispatcher start onboarding`.

It wakes the **PO & Analyst Agent** with `grill-with-docs` and `domain-modeling`. That agent
interviews you in rounds and populates `docs/CORE_DOCUMENT.md`. It does not proceed until you
agree the document is right.

It then derives `docs/specs/CORE_SPEC.md` and the first ADRs from it.

> If the project already has a core document, **port it rather than re-interviewing**. An
> interview that rediscovers decided things spends the one resource the setup cannot
> manufacture: your attention.

### C — Contracts frozen

The **Architect Agent** reads the core document and the specs, then writes the interface
types, schemas and API contracts on `dev` and freezes them. Feature work does not open until
this is done.

### D — Issues and dispatch

The PO & Analyst writes issues with `role:*` and skill labels. A webhook notifies the
Dispatcher, which creates the worktree:

```bash
orca worktree create feature/issue-42 --base dev
```

and invokes the **Developer Agent** with the skills the labels earned.

### E — Testing

The Dispatcher hands the worktree to the **Testing Agent** using `handoff`. It applies the
hybrid strategy — direct tests for standard code, the FMEA interview for large components
(see [section 11](#11-the-fmea-protocol)). Tests execute inside the container.

Pass → the Reviewer. Fail → back to the Developer, and the cycle count increments.

### F — Review and merge to `dev`

The **Reviewer Agent** checks the specification, the code standard, the passing tests and the
evidence gate, then merges into `dev` and deletes the worktree.

### G — Promotion to `main`

**You** inspect `dev` and open the pull request into `main` yourself. No agent ever does.

---

## 10. The circuit breaker

Three round trips between Developer and Tester/Reviewer. On the fourth the Dispatcher:

1. halts and hides the worktree,
2. posts `Circuit breaker tripped: 3 failed attempts.` on the issue,
3. reassigns the issue to the human with the diff and the error logs,
4. does **not** loop back to the Developer.

Do not raise the limit to force something through. An issue that has failed three times is
saying the specification is wrong, and a fourth attempt at the wrong thing costs more than
asking.

---

## 11. The FMEA protocol

For **large components** — data fetching, storage, core frontend — the Testing Agent stops
before writing anything and produces a failure matrix:

| Proposed Component | Failure Mode | Default Recovery Action | Human Confirmation Needed? |
| --- | --- | --- | --- |
| *component under test* | *how it can fail* | *what the system should do* | *yes / no* |

It presents the table to you in chat and asks for missing edge cases and custom recovery
actions. **Only after you confirm** does it write the suite, and the confirmed table becomes
the test list.

Why the interview exists: for a large component the interesting failures are the ones the
implementer did not think of, and the person who knows which failures matter is the owner, not
the agent. The matrix is the artefact of that conversation — commit it beside the tests.

Two standing warnings for the Testing Agent:

- **A fake that behaves better than the real thing proves nothing.** A test double that is
  more reliable, more ordered or more complete than the system it stands for will pass code
  that fails in production.
- **A test can pin a mistake.** If a test asserts the old behaviour and the change is right,
  the test is what changes — deliberately, in its own commit, with the reason stated.

---

## 12. Evidence before merge

A circuit breaker catches thrashing. It does not catch the failure that costs most: a run that
**reports success while being wrong**. Green tests prove the code does what the tests already
assumed, which is a different claim from "the code is right".

So the Reviewer may not merge on green tests alone. For changes touching data or a pipeline,
`evidence_gates` in `dispatch.yml` require a claim checked against something the change did
not produce:

- **counts reconciled** — a number from the run against a number from an independent source,
  both quoted in the pull request;
- **output sampled** — enough real rows pasted in that a wrong one would be visible;
- **cost measured** — against its budget, where one is documented.

The principle: **an artefact describes itself by observation, not by declaration.** A process
that reports on itself will report what it believes.

---

## 13. The daily digest

The digest is **derived from commit and pull-request history**, not from logs the agents
write. Agent-written logs get forgotten; two agents in parallel worktrees appending to one
daily file conflict on every merge; and a report assembled from self-description tells you
what each agent believed, which is the thing you are checking. Commits and pull requests exist
anyway and cannot be skipped.

### Contract for `mail/daily_digest.py`

| Aspect | Behaviour |
| --- | --- |
| Sources | `git log <branch> --since=<day> --until=<day+1> --no-merges`, and `gh pr list --base <branch> --state merged --json number,title,author,labels,mergedAt,url` |
| Grouping | Merged pull requests first, then commits grouped by author |
| Quiet day | Prints "nothing landed" and **sends no mail** |
| No credentials | Prints the rendered HTML instead of sending — always safe to run |
| Failure | Any missing tool, branch or auth degrades to "nothing from this source"; **never** exits non-zero |
| Escaping | Every value from a commit message, title or label is HTML-escaped |
| Encoding | `MIMEText(body, "html", "utf-8")` — the default is us-ascii and mangles punctuation |
| Fields split | Use control characters (`\x1f`, `\x1e`) in `--pretty=format`; a commit subject can contain any printable delimiter you might otherwise pick |

```bash
python mail/daily_digest.py --branch dev --print
python mail/daily_digest.py --branch dev --day 2026-01-15 --print
```

### `.github/workflows/daily-digest.yml`

```yaml
name: Daily agent digest

on:
  schedule:
    - cron: '10 18 * * *'      # off the hour: GitHub queues the hour heavily
  workflow_dispatch:
    inputs:
      day:
        description: 'Day to report, YYYY-MM-DD. Empty means today (UTC).'
        type: string
        default: ''
      print_only:
        description: 'Render into the log and send no mail'
        type: boolean
        default: true

concurrency:
  group: daily-digest
  cancel-in-progress: false

permissions:
  contents: read
  pull-requests: read

jobs:
  digest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0        # a shallow clone reports an empty day, every day
          ref: dev
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Render and send
        env:
          GH_TOKEN: ${{ github.token }}
          SMTP_SENDER_EMAIL: ${{ secrets.SMTP_SENDER_EMAIL }}
          SMTP_RECEIVER_EMAIL: ${{ secrets.SMTP_RECEIVER_EMAIL }}
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
          SMTP_SERVER: ${{ secrets.SMTP_SERVER }}
          SMTP_PORT: ${{ secrets.SMTP_PORT }}
        run: |
          args=(--branch dev)
          if [ -n "${{ inputs.day }}" ]; then args+=(--day "${{ inputs.day }}"); fi
          if [ "${{ inputs.print_only }}" = "true" ]; then args+=(--print); fi
          python mail/daily_digest.py "${args[@]}"
```

Leave the SMTP secrets unset at first: the scheduled run renders into the workflow log, which
is how you find out whether the content is worth reading before it starts arriving.

---

## 14. Branch protection

The human gate is a rule in the prompts **and** a rule on the repository. Set both — a prompt
is an instruction, a branch rule is an enforcement.

```bash
gh api -X PUT repos/:owner/:repo/branches/main/protection \
  -f "required_pull_request_reviews[required_approving_review_count]=1" \
  -F "enforce_admins=false" \
  -F "restrictions=null" \
  -F "required_status_checks=null"
```

Then confirm in repository settings: no direct pushes to `main`, pull request required.

---

## 15. Known tooling pitfalls

Verify each of these on your own machine. They commonly differ from what documentation and
plans claim, and every one of them fails the setup for everybody rather than for one person.

| Check | What is actually true |
| --- | --- |
| Orca via npm | Not published to npm. It is a host desktop application and cannot be containerised. |
| `skills install ... --target <dir>` | The verb is `add`, the flag is `--skill`; `--target` does not exist. |
| `--agent claude` | Rejected as invalid. The id is `claude-code`. |
| Base tag `1-22` clears the Node floor | It ships 22.16; the CLI declares 22.20.0. Pin upward with `n`. |
| Chaining after `n` | `n` swaps the node binary underneath the shell; a later `npm` in the same `RUN` resolves to the replaced binary. |
| Shell scripts from a Windows host | CRLF reaches bash unchanged because the container mounts the working tree, not a fresh checkout. Fails as `set: pipefail: invalid option name`. |
| `npm ci` over a bind mount | Replaces the host's `node_modules` with Linux binaries and breaks the host's tooling. Shadow it with a volume. |
| Skill names taken from a plan | Read them back with `--list` before committing them. |

---

## 16. Checklist

```
[ ] docs/CORE_DOCUMENT.md exists and the onboarding interview has populated it
[ ] docker info succeeds
[ ] Reopen in Container succeeds; node >= 22.20.0 and python answer
[ ] npx skills list shows every skill in the matrix
[ ] Large data mounts read-only; a write probe inside the container is refused
[ ] The project's own test suite passes inside the container
[ ] .orca/system_prompts/ holds all seven prompts
[ ] .orca/dispatch.yml names your labels, pipelines and gates
[ ] Every agent prompt forbids merging to main; only the Reviewer may merge to dev
[ ] gh labels created: ui seo scraper bug data trivial
[ ] python mail/daily_digest.py --print renders a real day
[ ] Branch protection on main: no direct pushes, pull request required
[ ] SMTP secrets set, or deliberately unset so the digest prints to the log
```
