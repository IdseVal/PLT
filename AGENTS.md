# PLT Agent Operating Guidelines

## Branch Rules

- Always branch off `dev`.

- Branch naming: `feature/<issue-number>-<short-description>` or `fix/<issue-number>-<short-description>`.

- You are ONLY allowed to merge into `dev` via an approved Pull Request.

- NEVER attempt to push directly to or merge into `main`.

## Definition of Done

A task is considered complete ONLY when:

1. Code compiles and linting passes.

2. Unit tests pass.

3. Every acceptance criterion listed in the GitHub Issue is addressed.

4. The PR description includes `Closes #<issue-number>`.

## Hand-off Rules

- If stuck on a task twice, add the label `needs-human` to the issue and stop execution.

- Never edit files inside another active worktree.