"""The daily digest of what the agents did, derived rather than reported.

Nothing here reads a log an agent wrote. Agents forget to log, two agents in parallel
worktrees writing one daily file conflict on every merge, and a report assembled from
self-description tells you what each agent believed rather than what happened. So the digest
is built from artefacts that exist anyway and cannot be skipped: the commits on the base
branch, and the pull requests that carried them.

This is the same rule the pipeline itself follows -- an artefact describes itself by
observation, not by declaration (``docs/architecture.md`` rule 2.11). A corpus manifest is
written by walking the corpus; a day's work is described by reading the day's commits.

Run with no credentials configured and it prints the report instead of sending it, which is
what makes it safe to try.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import smtplib
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

#: Separates the fields of one commit in ``git log`` output. A control character, because a
#: commit subject may contain anything a person can type, including whatever we might
#: otherwise have picked as a delimiter.
FIELD = "\x1f"

#: Separates commits, for the same reason.
RECORD = "\x1e"


@dataclass(frozen=True, slots=True)
class Commit:
    """One commit on the base branch."""

    sha: str
    author: str
    subject: str
    body: str

    @property
    def issue(self) -> str | None:
        """Return the issue this commit closes, if its message names one.

        Returns:
            The issue reference such as ``#42``, or ``None``. Read from the message rather
            than from an agent's report: a commit that closes an issue says so, because
            GitHub needs it to.
        """
        for token in f"{self.subject} {self.body}".replace("(", " ").replace(")", " ").split():
            if token.startswith("#") and token[1:].isdigit():
                return token
        return None


@dataclass
class Digest:
    """What one day's work amounted to."""

    day: str
    commits: list[Commit] = field(default_factory=list)
    pull_requests: list[dict[str, object]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Return whether anything happened. A quiet day sends no mail."""
        return not self.commits and not self.pull_requests


def run(command: list[str]) -> str:
    """Run a command and return its output, or an empty string if it fails.

    A digest is a convenience. It must never be the reason a scheduled job goes red, so a
    missing ``gh``, an unauthenticated shell or a repository without the branch all degrade
    to "nothing to report from this source" rather than to a traceback.

    Args:
        command: The command and its arguments.

    Returns:
        Standard output, stripped, or ``""`` on any failure.
    """
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"  ({' '.join(command[:2])} unavailable: {error})", file=sys.stderr)
        return ""
    if result.returncode != 0:
        print(f"  ({' '.join(command[:2])} exited {result.returncode})", file=sys.stderr)
        return ""
    return result.stdout.strip()


def collect_commits(branch: str, since: str, until: str) -> list[Commit]:
    """Read the commits landed on a branch within a window.

    Args:
        branch: Branch to read, e.g. ``dev``.
        since: Inclusive lower bound, ``YYYY-MM-DD``.
        until: Exclusive upper bound, ``YYYY-MM-DD``.

    Returns:
        One :class:`Commit` per commit, newest first.
    """
    raw = run(
        [
            "git",
            "log",
            branch,
            f"--since={since}",
            f"--until={until}",
            "--no-merges",
            f"--pretty=format:%h{FIELD}%an{FIELD}%s{FIELD}%b{RECORD}",
        ]
    )
    commits: list[Commit] = []
    for record in raw.split(RECORD):
        parts = record.strip("\n").split(FIELD)
        if len(parts) < 4 or not parts[0].strip():
            continue
        commits.append(
            Commit(sha=parts[0].strip(), author=parts[1], subject=parts[2], body=parts[3].strip())
        )
    return commits


def collect_pull_requests(base: str, day: str) -> list[dict[str, object]]:
    """Read the pull requests merged into a branch on a day.

    Args:
        base: Base branch, e.g. ``dev``.
        day: The day, ``YYYY-MM-DD``.

    Returns:
        One record per merged pull request. Empty when ``gh`` is unavailable, which is not an
        error: the commit list alone still makes a usable digest.
    """
    raw = run(
        [
            "gh",
            "pr",
            "list",
            "--base",
            base,
            "--state",
            "merged",
            "--limit",
            "50",
            "--json",
            "number,title,author,labels,mergedAt,url",
        ]
    )
    if not raw:
        return []
    try:
        everything = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [item for item in everything if str(item.get("mergedAt", "")).startswith(day)]


def render(digest: Digest) -> str:
    """Render the digest as HTML.

    Args:
        digest: The day's work.

    Returns:
        An HTML fragment. Every value that came from a commit message, a title or a label is
        escaped: this is text a person wrote, arriving in a document, and it is treated the
        way the rest of the project treats source text.
    """
    out = [f"<h2>Agent digest — {html.escape(digest.day)}</h2>"]

    if digest.pull_requests:
        out.append(f"<h3>Merged ({len(digest.pull_requests)})</h3><ul>")
        for pull in digest.pull_requests:
            labels = ", ".join(
                html.escape(str(label.get("name", ""))) for label in pull.get("labels") or []
            )
            author = html.escape(str((pull.get("author") or {}).get("login", "unknown")))
            out.append(
                f"<li><a href=\"{html.escape(str(pull.get('url', '')))}\">"
                f"#{html.escape(str(pull.get('number', '')))}</a> "
                f"{html.escape(str(pull.get('title', '')))}"
                f"<br/><small>{author}{' · ' + labels if labels else ''}</small></li>"
            )
        out.append("</ul>")

    if digest.commits:
        by_author: dict[str, list[Commit]] = {}
        for commit in digest.commits:
            by_author.setdefault(commit.author, []).append(commit)

        out.append(f"<h3>Commits on the base branch ({len(digest.commits)})</h3>")
        for author, commits in by_author.items():
            out.append(f"<h4>{html.escape(author)}</h4><ul>")
            for commit in commits:
                issue = f" ({html.escape(commit.issue)})" if commit.issue else ""
                out.append(
                    f"<li><code>{html.escape(commit.sha)}</code> "
                    f"{html.escape(commit.subject)}{issue}</li>"
                )
            out.append("</ul>")

    out.append(
        "<hr/><p><small>Derived from commit and pull-request history, not from anything an "
        "agent reported about itself.</small></p>"
    )
    return "\n".join(out)


def send(subject: str, body: str) -> bool:
    """Send the digest, or print it when no credentials are configured.

    Args:
        subject: Message subject.
        body: HTML body.

    Returns:
        Whether a message was actually sent.
    """
    sender = os.getenv("SMTP_SENDER_EMAIL")
    receiver = os.getenv("SMTP_RECEIVER_EMAIL")
    password = os.getenv("SMTP_PASSWORD")

    if not (sender and receiver and password):
        print("No SMTP credentials configured; printing the digest instead.\n")
        print(body)
        return False

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = receiver
    message.attach(MIMEText(body, "html", "utf-8"))

    with smtplib.SMTP(os.getenv("SMTP_SERVER", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "587"))) as server:
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, receiver, message.as_string())
    print(f"Digest sent to {receiver}.")
    return True


def main() -> int:
    """Build the digest for a day and send it if there is anything to say.

    Returns:
        Always ``0``. A digest that cannot be built is not a build failure, and a scheduled
        job that goes red on a quiet day is a job people learn to ignore.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", default="dev", help="Base branch to read. Default: dev.")
    parser.add_argument("--day", default=None, help="Day to report, YYYY-MM-DD. Default: today.")
    parser.add_argument("--print", action="store_true", help="Print; never send.")
    args = parser.parse_args()

    day = args.day or datetime.now(UTC).strftime("%Y-%m-%d")
    tomorrow = (datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC) + timedelta(days=1)).strftime(
        "%Y-%m-%d"
    )

    digest = Digest(
        day=day,
        commits=collect_commits(args.branch, day, tomorrow),
        pull_requests=collect_pull_requests(args.branch, day),
    )

    if digest.is_empty:
        print(f"No work landed on {args.branch} on {day}. Nothing sent.")
        return 0

    body = render(digest)
    if args.print:
        print(body)
        return 0

    send(f"Agent digest — {day}", body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
