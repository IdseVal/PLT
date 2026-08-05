"""The repository layout is a contract, so it is asserted rather than assumed.

``docs/architecture.md`` section 1 fixes the module tree that ten parallel work streams
build inside. This test fails the moment a required module is renamed or removed, and every
listed module is imported so that a stub can never be committed in a non-importable state.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from plt.config import Settings
from tests.conftest import REPO_ROOT

BACKEND = REPO_ROOT / "backend"

#: Every path named in architecture section 1, relative to the repository root.
REQUIRED_PATHS: tuple[str, ...] = (
    "backend/pyproject.toml",
    "backend/.env.example",
    "backend/alembic.ini",
    "backend/migrations",
    "backend/plt/__init__.py",
    "backend/plt/config.py",
    "backend/plt/app.py",
    "backend/plt/extensions.py",
    "backend/plt/db/base.py",
    "backend/plt/db/models.py",
    "backend/plt/db/session.py",
    "backend/plt/db/repositories.py",
    "backend/plt/api/__init__.py",
    "backend/plt/api/cases.py",
    "backend/plt/api/stats.py",
    "backend/plt/api/schemas.py",
    "backend/plt/api/errors.py",
    "backend/plt/pipeline/runner.py",
    "backend/plt/pipeline/base.py",
    "backend/plt/pipeline/checkpoint.py",
    "backend/plt/pipeline/dedup.py",
    "backend/plt/pipeline/filters/base.py",
    "backend/plt/pipeline/filters/keywords.py",
    "backend/plt/pipeline/connectors/rechtspraak.py",
    "backend/plt/pipeline/connectors/eurlex.py",
    "backend/plt/cli.py",
    "backend/plt/utils/logging.py",
    "backend/tests/unit",
    "backend/tests/integration",
    "backend/tests/fixtures",
    "frontend/package.json",
    "frontend/vite.config.ts",
    "frontend/tailwind.config.js",
    "frontend/tsconfig.json",
    "frontend/src/main.tsx",
    "frontend/src/App.tsx",
    "frontend/src/api/client.ts",
    "frontend/src/components",
    "frontend/src/pages",
    "frontend/src/hooks",
    "frontend/src/types",
    "frontend/src/styles",
    "frontend/tests",
    "data/keywords/schema.json",
    "docs/architecture.md",
    ".github/workflows/ci.yml",
    ".github/workflows/weekly-ingest.yml",
)

#: Every module of the ``plt`` package; each must import cleanly.
REQUIRED_MODULES: tuple[str, ...] = (
    "plt",
    "plt.app",
    "plt.cli",
    "plt.config",
    "plt.extensions",
    "plt.api",
    "plt.api.cases",
    "plt.api.errors",
    "plt.api.schemas",
    "plt.api.stats",
    "plt.db",
    "plt.db.base",
    "plt.db.models",
    "plt.db.repositories",
    "plt.db.session",
    "plt.pipeline",
    "plt.pipeline.base",
    "plt.pipeline.checkpoint",
    "plt.pipeline.dedup",
    "plt.pipeline.runner",
    "plt.pipeline.connectors",
    "plt.pipeline.connectors.eurlex",
    "plt.pipeline.connectors.rechtspraak",
    "plt.pipeline.filters",
    "plt.pipeline.filters.base",
    "plt.pipeline.filters.keywords",
    "plt.utils",
    "plt.utils.logging",
)


@pytest.mark.parametrize("relative_path", REQUIRED_PATHS)
def test_required_path_exists(relative_path: str) -> None:
    assert (REPO_ROOT / relative_path).exists(), (
        f"{relative_path} is required by docs/architecture.md section 1"
    )


@pytest.mark.parametrize("module_name", REQUIRED_MODULES)
def test_required_module_imports(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None


def test_env_files_are_git_ignored() -> None:
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert ".env" in ignored
    assert "!.env.example" in ignored


def test_env_example_documents_every_setting() -> None:
    documented = (BACKEND / ".env.example").read_text(encoding="utf-8")
    undocumented = [
        name
        for name in Settings.model_fields
        if f"PLT_{name.upper()}" not in documented
        # data_dir and keywords_dir are documented as commented-out overrides.
    ]

    assert undocumented == [], f".env.example is missing: {undocumented}"


def test_committed_keyword_lists_cover_the_launch_jurisdictions() -> None:
    keywords = REPO_ROOT / "data" / "keywords"

    assert sorted(path.name for path in Path(keywords).glob("*.json")) == [
        "eu.json",
        "nl.json",
        "schema.json",
    ]
