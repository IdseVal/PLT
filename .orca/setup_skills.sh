#!/usr/bin/env bash
#
# Fetch every agent skill this workspace dispatches, into the project scope.
#
# The CLI is `skills` from vercel-labs (npm: `skills`), and its verb is **add**, not install.
# There is no --target flag: scope is project (default, inside a project) or --global. Skills
# land in the agent directories the tool knows about, which is what --agent selects.
#
# Every skill name below was read back from the repository with `skills add <repo> --list` on
# 22 August 2026 rather than copied from a plan. One in the original plan did not exist:
# `diagnose` is really `diagnosing-bugs`. Re-run that check before adding a skill — a name
# that is wrong here fails the container build for everyone.
#
# Node >= 22.20.0 is required by the CLI. The image pins Node 22 for exactly this reason.
set -uo pipefail

SKILLS_CLI="${SKILLS_CLI:-npx -y skills@latest}"
AGENT="${SKILLS_AGENT:-claude}"

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

# Every role. Context compaction and clean transfer between agents.
add mattpocock/skills handoff

# Product owner and analyst: discovery, ubiquitous language, the first ADRs.
add mattpocock/skills grill-with-docs domain-modeling

# Architect: system boundaries, contracts, refactoring direction.
add mattpocock/skills improve-codebase-architecture codebase-design

# Developer. Loaded selectively by the dispatcher from the issue's labels — see
# .orca/dispatch.yml — so a typo fix does not pay for the whole design stack.
add anthropics/skills frontend-design
add vercel-labs/agent-skills web-design-guidelines
add leonxlnx/taste-skill high-end-visual-design
add coreyhaines31/marketingskills seo-audit
add scrapegraphai/just-scrape just-scrape
add mattpocock/skills diagnosing-bugs

# Tester.
add mattpocock/skills tdd
add anthropics/skills webapp-testing

# Reviewer.
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
