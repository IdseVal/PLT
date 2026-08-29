# The Dispatcher process

`dispatch.py` is the runtime that makes the workflow move. `~/.orca/roles/dispatcher.md`
records the *policy*; this script enforces it. Without it running, issues sit unrouted and
the pipeline stalls silently — everything else can look correctly installed.

It runs on the **host**, not in the dev container: it drives the `orca` CLI, which talks to
the Orca desktop runtime.

## Prerequisites

- Python 3.11+ with PyYAML (`python -c "import yaml"`).
- `gh` authenticated for this repo (`gh auth status`).
- Orca open, runtime reachable (`orca status --json` → `runtime.reachable: true`).
- The `ready` and `escalated` labels on the repo.
- `~/.orca/roles/` populated with the seven role files — once per machine, not per repo.

## Running it

```bash
python .orca/dispatcher/dispatch.py --once --dry-run   # decide, change nothing
python .orca/dispatcher/dispatch.py --once            # one tick, for Task Scheduler
python .orca/dispatcher/dispatch.py                   # foreground, Ctrl+C to stop
```

Start with `--dry-run`. It reads the issue list, computes the pipeline, the role and the
skills for each issue and reports what it would do, without creating a worktree or writing
a comment.

**Run only one at a time.** `state.json` is not locked between processes, so a foreground
poller and a scheduled `--once` running together will double-dispatch.

## What one tick does

1. Checks the Orca runtime is reachable. If not, the tick is skipped, not failed.
2. `gh issue list --state open --label ready`.
3. Drops issues that are `escalated`, and issues whose previous dispatch is still in flight.
   In-flight is decided by asking Orca which worktrees carry which `linkedIssue`, so a
   worktree renamed by hand still counts.
4. For each remaining issue: picks the pipeline (`trivial` when labelled and free of the
   labels that pipeline forbids, else `default`), takes the first stage as the role, gathers
   the skills that role and those labels earn, and builds the prompt from
   `~/.orca/roles/<role>.md` plus a DISPATCH CONTEXT block and the issue body.
5. `orca worktree create --agent claude --prompt <built prompt>`.
6. Records the dispatch, then comments on the issue.

State is written **before** the comment: a crash between the two costs a comment, not a
duplicate worktree.

## The circuit breaker

`circuit_breaker.max_cycles` in `../dispatch.yml`, default 3. Each worktree opened for an
issue is one cycle. On the request after the limit the poller does not create a worktree —
it comments `Circuit breaker tripped: N failed attempts.`, adds `escalated`, and never
touches that issue again.

Do not raise `max_cycles`. An issue that failed three times has a problem the next attempt
will not solve either.

To put an escalated issue back in play after a human has dealt with it:

```bash
python .orca/dispatcher/dispatch.py --reopen 107
```

That clears the in-flight and escalated flags but **keeps the cycle count**, so a reopened
issue that fails again trips the breaker immediately rather than getting three fresh tries.
Remove its `escalated` label on GitHub too.

## State

`state.json` (gitignored) holds, per issue: `cycles`, `open`, `worktree`, `last_dispatched`
and `escalated`. It is written atomically. If it is ever unreadable the poller renames it to
`state.corrupt-<epoch>.json` and starts empty rather than deleting it — it is the only
record of what was already dispatched, and losing it silently means double-dispatch.

Deleting `state.json` resets every cycle count to zero. That is a way to un-escalate
everything at once, and a way to dispatch work twice; prefer `--reopen`.

## Labels

The poller's two labels are named in `../dispatch.yml` under `dispatch:`, so a repository
with a different vocabulary changes configuration rather than code:

```yaml
dispatch:
  ready_label: ready
  escalated_label: escalated
```
