#!/usr/bin/env python3
"""Poll GitHub for approved issues and open an Orca worktree for each one.

This is the Dispatcher *runtime*. ``~/.orca/roles/dispatcher.md`` records the policy this
script enforces; the policy file is not itself executable, and an agent left to poll in a
loop would be slower, costlier and less predictable than the deterministic loop below.

Orca is a desktop application on this host, so there is nowhere for a GitHub webhook to
arrive. The Dispatcher polls ``gh`` and creates worktrees; it never receives events.

Run it on the HOST, not inside the dev container: it drives the ``orca`` CLI, which talks to
the desktop runtime.

    python .orca/dispatcher/dispatch.py                     # foreground, Ctrl+C to stop
    python .orca/dispatcher/dispatch.py --once              # one tick, for Task Scheduler
    python .orca/dispatcher/dispatch.py --once --dry-run    # decide, change nothing

State lives in ``.orca/dispatcher/state.json`` and is the resumption point: a tick that is
interrupted half way leaves the issues it already dispatched recorded, and the next tick
picks up from there. Exactly one process may run against a repository, because that file is
not locked between processes.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml

log = logging.getLogger("dispatcher")

#: Where this script lives, and the repository root above it.
HERE: Final[Path] = Path(__file__).resolve().parent
REPO_ROOT: Final[Path] = HERE.parent.parent

#: Host-level roles directory, shared by every project this workflow drives.
ROLES_DIR: Final[Path] = Path.home() / ".orca" / "roles"

#: Seconds between polls when running in the foreground.
DEFAULT_INTERVAL: Final[int] = 60

#: How long any single subprocess may take before it is abandoned.
COMMAND_TIMEOUT: Final[int] = 120

#: Longest issue body carried into a prompt. A very long issue is usually a discussion
#: thread; the agent is pointed at the issue rather than handed all of it.
MAX_BODY_CHARS: Final[int] = 8000

#: Set when a signal asks the loop to stop. Checked between ticks and between issues, so a
#: shutdown never interrupts a dispatch half way through recording it.
_stop = threading.Event()


class DispatchError(RuntimeError):
    """A step could not be completed. Never fatal to the loop; the next tick retries."""


# ------------------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Config:
    """The parts of ``.orca/dispatch.yml`` this runtime acts on.

    Attributes:
        base_branch: Branch every worktree is cut from.
        protected: Branches no agent may merge into. Carried into the prompt.
        worktree_prefix: Prefix for generated worktree names.
        ready_label: Label that marks an issue approved for dispatch.
        escalated_label: Label applied when the circuit breaker trips.
        max_cycles: Dispatches allowed per issue before the breaker trips.
        label_skills: Extra skills earned by each label.
        role_skills: Baseline skills for each role.
        pipelines: Pipeline name to its definition.
    """

    base_branch: str
    protected: tuple[str, ...]
    worktree_prefix: str
    ready_label: str
    escalated_label: str
    max_cycles: int
    label_skills: dict[str, tuple[str, ...]]
    role_skills: dict[str, tuple[str, ...]]
    pipelines: dict[str, dict[str, Any]]


def _skills_by_name(section: object) -> dict[str, tuple[str, ...]]:
    """Read a ``name -> {skills: [...]}`` mapping out of the configuration.

    Args:
        section: The ``roles`` or ``labels`` block, possibly absent.

    Returns:
        Skill tuples keyed by name.
    """
    if not isinstance(section, dict):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for name, body in section.items():
        skills = (body or {}).get("skills") or [] if isinstance(body, dict) else []
        out[str(name)] = tuple(str(skill) for skill in skills)
    return out


def load_config(path: Path) -> Config:
    """Read and validate the dispatch configuration.

    Args:
        path: Path to ``dispatch.yml``.

    Returns:
        The parsed configuration.

    Raises:
        DispatchError: The file is missing, unparsable, or missing a required key.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DispatchError(f"no dispatch configuration at {path}") from exc
    except yaml.YAMLError as exc:
        raise DispatchError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise DispatchError(f"{path} must contain a mapping")

    branches = raw.get("branches") or {}
    breaker = raw.get("circuit_breaker") or {}
    dispatch = raw.get("dispatch") or {}

    base = branches.get("base")
    if not base:
        raise DispatchError(f"{path}: branches.base is required")

    max_cycles = breaker.get("max_cycles", 3)
    if not isinstance(max_cycles, int) or max_cycles < 1:
        raise DispatchError(f"{path}: circuit_breaker.max_cycles must be a positive integer")

    return Config(
        base_branch=str(base),
        protected=tuple(str(branch) for branch in branches.get("protected") or ()),
        worktree_prefix=str(branches.get("worktree_prefix") or "feature/"),
        ready_label=str(dispatch.get("ready_label") or "ready"),
        escalated_label=str(dispatch.get("escalated_label") or "escalated"),
        max_cycles=max_cycles,
        label_skills=_skills_by_name(raw.get("labels")),
        role_skills=_skills_by_name(raw.get("roles")),
        pipelines=dict(raw.get("pipelines") or {}),
    )


# ------------------------------------------------------------------------------------------
# State
# ------------------------------------------------------------------------------------------


@dataclass
class IssueState:
    """What has happened to one issue.

    Attributes:
        cycles: Worktrees opened for this issue so far.
        open: Whether the most recent dispatch is still in flight.
        worktree: Name of the most recent worktree.
        last_dispatched: ISO timestamp of the most recent dispatch.
        escalated: Whether the breaker has tripped and the poller has let go.
    """

    cycles: int = 0
    open: bool = False
    worktree: str = ""
    last_dispatched: str = ""
    escalated: bool = False


@dataclass
class State:
    """Everything the poller remembers between ticks."""

    issues: dict[int, IssueState] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> State:
        """Read state from disk, tolerating absence and corruption.

        A state file that cannot be read is renamed aside rather than deleted: it is the only
        record of what was already dispatched, and losing it silently means double-dispatch.

        Args:
            path: Path to ``state.json``.

        Returns:
            The stored state, or an empty one.
        """
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            issues = {
                int(number): IssueState(**body)
                for number, body in (raw.get("issues") or {}).items()
            }
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            spoiled = path.with_suffix(f".corrupt-{int(time.time())}.json")
            path.rename(spoiled)
            log.error("state file unreadable (%s); moved to %s and starting empty", exc, spoiled)
            return cls()
        return cls(issues=issues)

    def save(self, path: Path) -> None:
        """Write state atomically, so an interrupted write cannot truncate it.

        Args:
            path: Path to ``state.json``.
        """
        payload = {
            "version": 1,
            "updated": datetime.now(UTC).isoformat(timespec="seconds"),
            "issues": {str(n): vars(s) for n, s in sorted(self.issues.items())},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)


# ------------------------------------------------------------------------------------------
# Subprocess helpers
# ------------------------------------------------------------------------------------------


def main_worktree(start: Path) -> Path:
    """Find the repository's main checkout, from anywhere inside it.

    Orca registers the main checkout, not the linked worktrees cut from it. Running the
    poller from inside a worktree - which is exactly where an agent runs - would otherwise
    hand Orca a path it has never heard of and every dispatch would fail ``repo_not_found``.

    ``--git-common-dir`` resolves to the shared ``.git`` of the main checkout from any linked
    worktree, so its parent is the path Orca knows.

    Args:
        start: Any directory inside the repository.

    Returns:
        The main checkout, or ``start`` unchanged when git cannot answer.
    """
    try:
        common = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return start
    if common.returncode != 0 or not common.stdout.strip():
        return start
    return Path(common.stdout.strip()).parent


def _executable(name: str) -> str:
    """Resolve a command to a full path.

    On Windows ``gh`` and ``orca`` are shims that :func:`subprocess.run` will not find
    without their extension, and resolving here keeps every call site free of ``shell=True``.

    Args:
        name: Command name.

    Returns:
        Absolute path to the executable.

    Raises:
        DispatchError: The command is not on PATH.
    """
    found = shutil.which(name)
    if not found:
        raise DispatchError(f"{name} is not on PATH")
    return found


def run_command(command: list[str], *, check: bool = True) -> str:
    """Run a command and return its stdout.

    Arguments are always passed as a list, never through a shell, so an issue title
    containing shell metacharacters is data rather than code.

    Args:
        command: Command and arguments; element zero is resolved on PATH.
        check: Raise when the command exits non-zero.

    Returns:
        Captured stdout, stripped. Empty when the command failed and ``check`` is unset.

    Raises:
        DispatchError: The command failed and ``check`` is set, or it timed out.
    """
    resolved = [_executable(command[0]), *command[1:]]
    try:
        completed = subprocess.run(
            resolved,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DispatchError(f"{command[0]} timed out after {COMMAND_TIMEOUT}s") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:400]
        if check:
            raise DispatchError(f"{' '.join(command[:3])} exited {completed.returncode}: {detail}")
        log.debug("%s exited %s: %s", command[0], completed.returncode, detail)
        return ""
    return completed.stdout.strip()


def run_json(command: list[str]) -> Any:
    """Run a command and parse its stdout as JSON.

    Args:
        command: Command and arguments.

    Returns:
        The decoded payload.

    Raises:
        DispatchError: The command failed or did not emit JSON.
    """
    raw = run_command(command)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DispatchError(f"{command[0]} did not return JSON: {raw[:200]}") from exc


# ------------------------------------------------------------------------------------------
# GitHub and Orca
# ------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Issue:
    """An open issue approved for dispatch."""

    number: int
    title: str
    body: str
    labels: tuple[str, ...]


def ready_issues(config: Config, repo: str | None) -> list[Issue]:
    """List open issues carrying the ready label.

    Args:
        config: Dispatch configuration.
        repo: ``owner/name``, or ``None`` to let ``gh`` infer it from the checkout.

    Returns:
        One :class:`Issue` per approved issue, lowest number first.

    Raises:
        DispatchError: ``gh`` failed or returned something other than JSON.
    """
    command = [
        "gh", "issue", "list",
        "--state", "open",
        "--label", config.ready_label,
        "--limit", "100",
        "--json", "number,title,body,labels",
    ]
    if repo:
        command += ["--repo", repo]

    issues = [
        Issue(
            number=int(item["number"]),
            title=str(item.get("title") or ""),
            body=str(item.get("body") or ""),
            labels=tuple(str(label["name"]) for label in item.get("labels") or []),
        )
        for item in run_json(command)
    ]
    return sorted(issues, key=lambda issue: issue.number)


def comment_on_issue(number: int, body: str, repo: str | None) -> None:
    """Post a comment on an issue. A failure here is logged, never fatal.

    Args:
        number: Issue number.
        body: Comment text.
        repo: ``owner/name``, or ``None``.
    """
    command = ["gh", "issue", "comment", str(number), "--body", body]
    if repo:
        command += ["--repo", repo]
    run_command(command, check=False)


def add_label(number: int, label: str, repo: str | None) -> None:
    """Add a label to an issue. A failure here is logged, never fatal.

    Args:
        number: Issue number.
        label: Label to add.
        repo: ``owner/name``, or ``None``.
    """
    command = ["gh", "issue", "edit", str(number), "--add-label", label]
    if repo:
        command += ["--repo", repo]
    run_command(command, check=False)


def runtime_ready() -> bool:
    """Report whether the Orca desktop runtime can be reached.

    Returns:
        True when Orca is running and its runtime is reachable.
    """
    try:
        payload = run_json(["orca", "status", "--json"])
    except DispatchError as exc:
        log.warning("orca status failed: %s", exc)
        return False
    runtime = (payload.get("result") or {}).get("runtime") or {}
    return bool(runtime.get("reachable"))


def issues_in_flight() -> set[int]:
    """Read which issue numbers already have an Orca worktree.

    The worktree's own ``linkedIssue`` is the source of truth rather than its name: a
    worktree renamed by hand still belongs to its issue.

    Returns:
        Issue numbers with a live worktree. Empty when Orca cannot be asked, which makes the
        caller fall back to its own record rather than dispatching a second time.
    """
    try:
        payload = run_json(["orca", "worktree", "list", "--json"])
    except DispatchError as exc:
        log.warning("orca worktree list failed: %s", exc)
        return set()

    live: set[int] = set()
    for worktree in (payload.get("result") or {}).get("worktrees") or []:
        linked = worktree.get("linkedIssue")
        if isinstance(linked, bool):
            continue
        if isinstance(linked, int):
            live.add(linked)
        elif isinstance(linked, dict) and isinstance(linked.get("number"), int):
            live.add(int(linked["number"]))
    return live


def create_worktree(name: str, issue: Issue, prompt: str, config: Config, repo_path: Path) -> None:
    """Ask Orca to open a worktree and start an agent in it.

    Args:
        name: Worktree name.
        issue: The issue being dispatched.
        prompt: Full prompt for the agent.
        config: Dispatch configuration.
        repo_path: Filesystem path of the repository.

    Raises:
        DispatchError: Orca refused to create the worktree.
    """
    run_command(
        [
            "orca", "worktree", "create",
            "--repo", f"path:{repo_path.as_posix()}",
            "--name", name,
            "--issue", str(issue.number),
            "--base-branch", config.base_branch,
            "--agent", "claude",
            "--prompt", prompt,
            "--json",
        ]
    )


# ------------------------------------------------------------------------------------------
# Routing
# ------------------------------------------------------------------------------------------


def pipeline_for(issue: Issue, config: Config) -> str:
    """Choose the pipeline an issue runs through.

    ``trivial`` is a request, not a permission: an issue that also carries a label the
    pipeline forbids runs the default pipeline instead, and a test author is asked for.

    Args:
        issue: The issue.
        config: Dispatch configuration.

    Returns:
        Pipeline name.
    """
    if "trivial" not in issue.labels:
        return "default"
    forbidden = set(config.pipelines.get("trivial", {}).get("forbid_labels") or ())
    clashes = forbidden.intersection(issue.labels)
    if clashes:
        log.info(
            "issue #%s asked for the trivial pipeline but carries %s; running default",
            issue.number,
            ", ".join(sorted(clashes)),
        )
        return "default"
    return "trivial"


def skills_for(issue: Issue, role: str, config: Config) -> tuple[str, ...]:
    """Compute the skills a role earns on this issue.

    Args:
        issue: The issue.
        role: Role about to be dispatched.
        config: Dispatch configuration.

    Returns:
        Skill names, deduplicated, baseline first and label-earned after.
    """
    seen: dict[str, None] = dict.fromkeys(config.role_skills.get(role, ()))
    for label in issue.labels:
        for skill in config.label_skills.get(label, ()):
            seen.setdefault(skill, None)
    return tuple(seen)


def build_prompt(issue: Issue, role: str, pipeline: str, cycle: int, config: Config) -> str:
    """Assemble the prompt handed to the agent.

    The role prompt is read from the shared host-level roles directory, so every project
    dispatches the same agent definition and a fix to one is a fix to all.

    Args:
        issue: The issue being dispatched.
        role: Role to dispatch, e.g. ``developer``.
        pipeline: Pipeline name.
        cycle: Which attempt this is, counting from one.
        config: Dispatch configuration.

    Returns:
        The full prompt.

    Raises:
        DispatchError: The role prompt is missing from ``~/.orca/roles/``.
    """
    role_file = ROLES_DIR / f"{role}.md"
    try:
        role_text = role_file.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DispatchError(
            f"no role prompt at {role_file}; populate ~/.orca/roles/ once per machine"
        ) from exc

    body = issue.body.strip()
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n\n[...truncated; read the issue for the rest]"

    skills = skills_for(issue, role, config)
    stages = config.pipelines.get(pipeline, {}).get("stages") or []

    return "\n".join(
        [
            role_text.rstrip(),
            "",
            "---",
            "",
            "## DISPATCH CONTEXT",
            "",
            f"- Issue: #{issue.number} - {issue.title}",
            f"- Role: {role}",
            f"- Pipeline: {pipeline} ({' -> '.join(str(stage) for stage in stages)})",
            f"- Attempt: {cycle} of {config.max_cycles}",
            f"- Labels: {', '.join(issue.labels) or 'none'}",
            f"- Skills earned: {', '.join(skills) or 'none beyond the baseline'}",
            f"- Base branch: {config.base_branch}",
            f"- Never merge into: {', '.join(config.protected) or 'main'}",
            "",
            "## ISSUE",
            "",
            body or "(the issue has no body; read it on GitHub before starting)",
        ]
    )


# ------------------------------------------------------------------------------------------
# The loop
# ------------------------------------------------------------------------------------------


def trip_breaker(issue: Issue, record: IssueState, config: Config, repo: str | None) -> None:
    """Escalate an issue to a human and stop touching it.

    Args:
        issue: The issue.
        record: Its state, mutated in place.
        config: Dispatch configuration.
        repo: ``owner/name``, or ``None``.
    """
    log.error("circuit breaker tripped on issue #%s after %s attempts", issue.number, record.cycles)
    comment_on_issue(
        issue.number,
        f"Circuit breaker tripped: {record.cycles} failed attempts.\n\n"
        "The dispatcher will not open another worktree for this issue. A human needs to read "
        "what the previous attempts produced before it goes round again.",
        repo,
    )
    add_label(issue.number, config.escalated_label, repo)
    record.escalated = True
    record.open = False


def tick(config: Config, state: State, args: argparse.Namespace) -> int:
    """Run one poll-and-dispatch pass.

    Args:
        config: Dispatch configuration.
        state: Poller state, mutated and saved as it goes.
        args: Parsed command line.

    Returns:
        How many worktrees were created, or would have been in a dry run.

    Raises:
        DispatchError: The issue list could not be read. The caller retries next tick.
    """
    if not args.dry_run and not runtime_ready():
        log.warning("Orca runtime is not reachable; skipping this tick")
        return 0

    issues = ready_issues(config, args.repo)
    if not issues:
        log.info("no open issues carry the '%s' label", config.ready_label)
        return 0

    live = issues_in_flight()
    dispatched = 0

    for issue in issues:
        if _stop.is_set():
            log.info("stop requested; leaving the remaining issues for the next tick")
            break

        record = state.issues.setdefault(issue.number, IssueState())

        if record.escalated or config.escalated_label in issue.labels:
            log.debug("issue #%s is escalated; leaving it alone", issue.number)
            continue

        if record.open:
            if issue.number in live:
                log.debug("issue #%s is still in flight in %s", issue.number, record.worktree)
                continue
            log.info(
                "issue #%s: worktree %s is gone, attempt %s is over",
                issue.number, record.worktree or "(unnamed)", record.cycles,
            )
            record.open = False

        if record.cycles >= config.max_cycles:
            if args.dry_run:
                log.error("DRY RUN: would trip the circuit breaker on #%s", issue.number)
            else:
                trip_breaker(issue, record, config, args.repo)
                state.save(args.state)
            continue

        pipeline = pipeline_for(issue, config)
        stages = config.pipelines.get(pipeline, {}).get("stages") or ["developer"]
        role = str(stages[0])
        cycle = record.cycles + 1
        name = f"{config.worktree_prefix}issue-{issue.number}"

        try:
            prompt = build_prompt(issue, role, pipeline, cycle, config)
        except DispatchError as exc:
            log.error("cannot dispatch #%s: %s", issue.number, exc)
            continue

        if args.dry_run:
            log.info(
                "DRY RUN: would dispatch #%s to %s as '%s' "
                "(pipeline %s, attempt %s of %s, prompt %s chars, skills: %s)",
                issue.number, role, name, pipeline, cycle, config.max_cycles, len(prompt),
                ", ".join(skills_for(issue, role, config)) or "baseline only",
            )
            dispatched += 1
            continue

        try:
            create_worktree(name, issue, prompt, config, args.repo_path)
        except DispatchError as exc:
            log.error("Orca refused a worktree for #%s: %s", issue.number, exc)
            continue

        record.cycles = cycle
        record.open = True
        record.worktree = name
        record.last_dispatched = datetime.now(UTC).isoformat(timespec="seconds")
        # Saved before the comment: a crash between the two costs a comment, not a second
        # worktree on the next tick.
        state.save(args.state)

        comment_on_issue(
            issue.number,
            f"Dispatched to the **{role}** in worktree `{name}` "
            f"(pipeline `{pipeline}`, attempt {cycle} of {config.max_cycles}).",
            args.repo,
        )
        log.info("dispatched #%s to %s in %s (attempt %s)", issue.number, role, name, cycle)
        dispatched += 1

    return dispatched


def install_signal_handlers() -> None:
    """Ask the loop to stop at the next safe boundary when interrupted."""

    def handler(signum: int, _frame: object) -> None:
        log.info("signal %s received; finishing the current issue, then stopping", signum)
        _stop.set()

    signal.signal(signal.SIGINT, handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handler)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv: Arguments, or ``None`` to read ``sys.argv``.

    Returns:
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Poll GitHub for approved issues and open an Orca worktree for each.",
    )
    parser.add_argument("--once", action="store_true", help="Run one tick and exit.")
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL,
        help=f"Seconds between polls. Default: {DEFAULT_INTERVAL}.",
    )
    parser.add_argument(
        "--repo", default=None,
        help="owner/name for gh. Default: inferred from the checkout.",
    )
    parser.add_argument(
        "--repo-path", type=Path, default=None,
        help="Repository path handed to Orca. Default: the main checkout, found from here.",
    )
    parser.add_argument(
        "--config", type=Path, default=REPO_ROOT / ".orca" / "dispatch.yml",
        help="Path to dispatch.yml.",
    )
    parser.add_argument(
        "--state", type=Path, default=HERE / "state.json",
        help="Path to the state file.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Decide and report; create no worktree and comment nowhere.",
    )
    parser.add_argument(
        "--reopen", type=int, metavar="ISSUE", default=None,
        help="Clear the in-flight and escalated flags for one issue, then exit.",
    )
    parser.add_argument("--verbose", action="store_true", help="Log at DEBUG.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Arguments, or ``None`` to read ``sys.argv``.

    Returns:
        ``0`` on a clean run or a clean shutdown, ``1`` on a configuration error.
    """
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        config = load_config(args.config)
    except DispatchError as exc:
        log.critical("%s", exc)
        return 1

    if args.repo_path is None:
        args.repo_path = main_worktree(REPO_ROOT)

    state = State.load(args.state)

    if args.reopen is not None:
        record = state.issues.get(args.reopen)
        if record is None:
            log.error("issue #%s is not in the state file", args.reopen)
            return 1
        record.open = False
        record.escalated = False
        state.save(args.state)
        log.info("issue #%s reopened for dispatch (%s attempts so far)", args.reopen, record.cycles)
        return 0

    install_signal_handlers()
    log.info(
        "dispatcher up: repo=%s base=%s ready=%s max_cycles=%s%s",
        args.repo_path, config.base_branch, config.ready_label, config.max_cycles,
        " (DRY RUN)" if args.dry_run else "",
    )

    while not _stop.is_set():
        try:
            tick(config, state, args)
        except DispatchError as exc:
            log.error("tick failed: %s", exc)
        # A poller that dies on one bad tick is worse than one that logs and retries.
        except Exception:
            log.exception("unexpected error in tick")

        if args.once:
            break
        # Interruptible sleep: Ctrl+C is answered at once, not after the whole interval.
        _stop.wait(args.interval)

    log.info("dispatcher stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
