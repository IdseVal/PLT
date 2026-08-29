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
