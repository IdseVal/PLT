"""One chain of migrations, judged on the merge rather than on the branch.

Two pull requests that each add a revision both branch from the same head, so both number
their file ``000N`` and both point ``down_revision`` at ``000N-1``. Each is a valid chain on
its own branch, and each migrates cleanly there; together they are two heads, and
``alembic upgrade head`` then refuses to run at all, so no fresh database can be created.
That is how ``dev`` came to hold two ``0004`` revisions (issue #79).

Alembic does say something when it happens — ``Multiple heads are present for given argument
'head'; 0004, 0004`` — but it never names a file, and it says it from inside a traceback
twenty screens long. The checks here name the files, and run before anything that needs a
database.

**The collision exists only in the merged tree, so the checks have to see the merged tree.**
CI runs them on the merge commit GitHub builds for a pull request, and points
:data:`EXTRA_DIRS_ENV` at any revision that landed on the base branch *after* that merge
commit was built — GitHub does not rebuild it every time the base moves, and a run judged
against a stale base is exactly what let the two ``0004`` revisions through. See the
"Migrations" step in ``.github/workflows/ci.yml``. Locally, a plain ``pytest`` checks the
working tree, which is the merged tree once ``dev`` has been merged in.

``tests/unit/test_migrations.py`` covers the other half: that the chain these checks prove
single-headed is also traversable, up to head, back down to base and up again.
"""

from __future__ import annotations

import ast
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

#: The revision files alembic itself reads.
VERSIONS_DIR = REPO_ROOT / "backend" / "migrations" / "versions"

#: Further directories of revision files to fold in, separated by :data:`os.pathsep`. CI sets
#: it; a developer does not need to.
EXTRA_DIRS_ENV = "MIGRATION_GRAPH_EXTRA_DIRS"

#: Appended to every failure here, because the resolution is the same one every time and
#: getting it wrong the other way round breaks databases that are already migrated.
COLLISION_HINT = (
    "Renumber the *later* migration and repoint its down_revision at the earlier one; leave "
    "the earlier identifier alone, because every database that has already run it stores "
    "that identifier in alembic_version. CONTRIBUTING.md, 'When two branches add a migration "
    "at the same time', has the steps."
)


@dataclass(frozen=True)
class Revision:
    """A revision file's identifier and the revisions it is applied after."""

    path: Path
    identifier: str
    parents: tuple[str, ...]


def module_level_literal(module: ast.Module, name: str) -> object:
    """Return the literal a module assigns to a top-level name.

    Reading the value out of the syntax tree rather than importing the module keeps the check
    independent of alembic, which is the point: alembic loads a duplicated revision without
    complaint and only fails later, deep in a command, without naming either file.

    Args:
        module: The parsed revision file.
        name: The variable to look for, ``revision`` or ``down_revision``.

    Returns:
        The assigned literal, or ``None`` if the module never assigns that name.
    """
    for node in module.body:
        if isinstance(node, ast.AnnAssign):
            target: ast.expr | None = node.target
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id == name and value is not None:
            return ast.literal_eval(value)
    return None


def parent_identifiers(value: object) -> tuple[str, ...]:
    """Normalise a ``down_revision`` value to a tuple of identifiers.

    Args:
        value: ``None`` for a base revision, a string for the usual single parent, or a
            sequence of strings for a merge revision.

    Returns:
        The parent identifiers, empty for a base revision.

    Raises:
        TypeError: If the value is none of those shapes.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value)
    detail = f"down_revision must be None, a string or a sequence of strings, not {value!r}"
    raise TypeError(detail)


def read_revisions(directory: Path) -> list[Revision]:
    """Read every revision file in a directory.

    Args:
        directory: A directory of alembic revision files. A missing directory reads as empty,
            so CI can point at a scratch directory it may not have needed to create.

    Returns:
        One :class:`Revision` per ``.py`` file, ordered by file name.

    Raises:
        ValueError: If a file assigns no ``revision`` identifier, which alembic would treat
            as not being a revision file at all.
    """
    if not directory.is_dir():
        return []

    revisions: list[Revision] = []
    for path in sorted(directory.glob("*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        identifier = module_level_literal(module, "revision")
        if not isinstance(identifier, str):
            detail = f"{path} assigns no revision identifier"
            raise ValueError(detail)
        parents = parent_identifiers(module_level_literal(module, "down_revision"))
        revisions.append(Revision(path=path, identifier=identifier, parents=parents))
    return revisions


def extra_directories() -> list[Path]:
    """Return the directories named by :data:`EXTRA_DIRS_ENV`, which may be none."""
    raw = os.environ.get(EXTRA_DIRS_ENV, "")
    return [Path(entry) for entry in raw.split(os.pathsep) if entry]


def revisions_under_test() -> list[Revision]:
    """Return the revision files this run has to judge as one set."""
    directories = [VERSIONS_DIR, *extra_directories()]
    return [revision for directory in directories for revision in read_revisions(directory)]


def duplicate_identifiers(revisions: Sequence[Revision]) -> dict[str, list[Path]]:
    """Return each identifier claimed by more than one file, with the files claiming it."""
    by_identifier: dict[str, list[Path]] = {}
    for revision in revisions:
        by_identifier.setdefault(revision.identifier, []).append(revision.path)
    return {identifier: paths for identifier, paths in by_identifier.items() if len(paths) > 1}


def heads(revisions: Sequence[Revision]) -> list[Revision]:
    """Return the revisions no other revision is applied after."""
    referenced = {parent for revision in revisions for parent in revision.parents}
    return [revision for revision in revisions if revision.identifier not in referenced]


def bases(revisions: Sequence[Revision]) -> list[Revision]:
    """Return the revisions that follow nothing."""
    return [revision for revision in revisions if not revision.parents]


def unknown_parents(revisions: Sequence[Revision]) -> list[tuple[Revision, str]]:
    """Return every ``(revision, parent)`` pair whose parent is not a revision that exists."""
    known = {revision.identifier for revision in revisions}
    return [
        (revision, parent)
        for revision in revisions
        for parent in revision.parents
        if parent not in known
    ]


def display(path: Path) -> str:
    """Return a path relative to the repository root when it lies inside it."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def describe(revisions: Sequence[Revision]) -> str:
    """Return one indented ``identifier — file`` line per revision."""
    return "\n".join(
        f"    {revision.identifier}  {display(revision.path)}" for revision in revisions
    )


# --------------------------------------------------------------------------------------
# The guard itself, over whatever revision files this run has been given.
# --------------------------------------------------------------------------------------


def test_no_two_migrations_claim_the_same_revision_identifier() -> None:
    clashes = duplicate_identifiers(revisions_under_test())
    report = "\n".join(
        f'  revision "{identifier}" is claimed by:\n'
        + "\n".join(f"    {display(path)}" for path in paths)
        for identifier, paths in sorted(clashes.items())
    )
    assert not clashes, (
        f"Two migrations claim the same revision identifier:\n{report}\n\n{COLLISION_HINT}"
    )


def test_the_migrations_form_exactly_one_head() -> None:
    found = heads(revisions_under_test())
    assert len(found) == 1, (
        f"The migrations form {len(found)} heads; alembic upgrade head cannot run and no "
        f"fresh database can be created. The heads are:\n{describe(found)}\n\n{COLLISION_HINT}"
    )


def test_the_migrations_form_exactly_one_base() -> None:
    found = bases(revisions_under_test())
    assert len(found) == 1, (
        f"The migrations form {len(found)} bases; alembic downgrade base cannot run. Every "
        f"revision but the first needs a down_revision:\n{describe(found)}"
    )


def test_every_down_revision_names_a_revision_that_exists() -> None:
    dangling = unknown_parents(revisions_under_test())
    report = "\n".join(
        f'    {display(revision.path)} follows "{parent}", which no file defines'
        for revision, parent in dangling
    )
    assert not dangling, (
        f"A migration is applied after a revision that does not exist, so alembic cannot "
        f"resolve the chain:\n{report}\n\n{COLLISION_HINT}"
    )


# --------------------------------------------------------------------------------------
# The guard has to be seen to fire, on revision files written for the purpose.
# --------------------------------------------------------------------------------------


def write_revision(directory: Path, identifier: str, down_revision: str | None) -> Path:
    """Write a revision file with no migration in it, for the checks above to read.

    Args:
        directory: Where to write it. Created if it does not exist.
        identifier: The ``revision`` value.
        down_revision: The ``down_revision`` value, ``None`` for a base revision.

    Returns:
        The path written.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{identifier}_generated.py"
    parent = "None" if down_revision is None else f'"{down_revision}"'
    path.write_text(
        f'"""Generated for a test."""\n\nrevision: str = "{identifier}"\n'
        f"down_revision: str | None = {parent}\n",
        encoding="utf-8",
    )
    return path


def test_the_real_migrations_are_read_at_all() -> None:
    """The premise of every check above: the directory is found and it holds revisions."""
    revisions = read_revisions(VERSIONS_DIR)
    assert len(revisions) >= 3
    assert all(revision.identifier for revision in revisions)


def test_two_files_claiming_one_identifier_are_both_named(tmp_path: Path) -> None:
    first = write_revision(tmp_path / "a", "0004", "0003")
    second = write_revision(tmp_path / "b", "0004", "0003")

    clashes = duplicate_identifiers(read_revisions(tmp_path / "a") + read_revisions(tmp_path / "b"))

    assert clashes == {"0004": [first, second]}


def test_a_fork_is_reported_as_two_heads(tmp_path: Path) -> None:
    write_revision(tmp_path, "0001", None)
    write_revision(tmp_path, "0002", "0001")
    write_revision(tmp_path, "0003", "0001")

    assert sorted(revision.identifier for revision in heads(read_revisions(tmp_path))) == [
        "0002",
        "0003",
    ]


def test_a_single_chain_has_one_head_and_one_base(tmp_path: Path) -> None:
    write_revision(tmp_path, "0001", None)
    write_revision(tmp_path, "0002", "0001")
    write_revision(tmp_path, "0003", "0002")
    revisions = read_revisions(tmp_path)

    assert [revision.identifier for revision in heads(revisions)] == ["0003"]
    assert [revision.identifier for revision in bases(revisions)] == ["0001"]
    assert unknown_parents(revisions) == []


def test_a_down_revision_pointing_nowhere_is_reported(tmp_path: Path) -> None:
    write_revision(tmp_path, "0001", None)
    orphan = write_revision(tmp_path, "0002", "0009")

    assert unknown_parents(read_revisions(tmp_path)) == [
        (Revision(path=orphan, identifier="0002", parents=("0009",)), "0009")
    ]


def test_a_merge_revision_counts_as_following_both_parents(tmp_path: Path) -> None:
    """``down_revision`` may be a tuple; a revision map that ignored that would see two heads."""
    write_revision(tmp_path, "0001", None)
    write_revision(tmp_path, "0002", "0001")
    write_revision(tmp_path, "0003", "0001")
    (tmp_path / "0004_generated.py").write_text(
        '"""Generated for a test."""\n\nrevision: str = "0004"\n'
        'down_revision: tuple[str, ...] | None = ("0002", "0003")\n',
        encoding="utf-8",
    )

    assert [revision.identifier for revision in heads(read_revisions(tmp_path))] == ["0004"]


def test_a_missing_directory_reads_as_empty(tmp_path: Path) -> None:
    """CI names a scratch directory it may never have needed to create."""
    assert read_revisions(tmp_path / "never-created") == []


def test_the_extra_directory_is_folded_in(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The mechanism CI uses to judge this branch against migrations that landed after it.

    Without this, the guard would only ever see the working tree, and a revision merged into
    the base branch after the merge commit was built — the shape that put two ``0004``
    revisions on ``dev`` — would stay invisible to it.
    """
    real = read_revisions(VERSIONS_DIR)
    latest = real[-1]
    monkeypatch.setenv(EXTRA_DIRS_ENV, str(tmp_path))
    write_revision(tmp_path, latest.identifier, latest.parents[0] if latest.parents else None)

    assert extra_directories() == [tmp_path]
    assert len(revisions_under_test()) == len(real) + 1
    assert latest.identifier in duplicate_identifiers(revisions_under_test())
