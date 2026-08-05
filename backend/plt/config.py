"""Application settings, loaded from the environment.

``docs/architecture.md`` section 2.1 makes configuration external without exception: no URL,
path, credential, page size or rate limit may be hard-coded elsewhere in the code base.
Every setting below is overridable through an environment variable prefixed with ``PLT_``
(or an entry in a ``.env`` file), and every one of them is documented in ``.env.example``.

Read settings through :func:`get_settings`, which caches a single validated instance for the
process, rather than by instantiating :class:`Settings` directly.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Final

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

__all__ = [
    "AppEnv",
    "EurLexDiscoveryDate",
    "LogFormat",
    "Settings",
    "get_settings",
]

#: Repository root, resolved from this file: ``<repo>/backend/plt/config.py``.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

#: Placeholder secret shipped in ``.env.example``; refused outside development.
_PLACEHOLDER_SECRET_KEY: Final[str] = "change-me-in-production"  # noqa: S105

#: Shape of a controlled-vocabulary code that may be interpolated into a SPARQL URI.
_VOCABULARY_CODE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_]{1,32}")

#: A list-of-strings setting that reaches its validators as the raw configured string.
#:
#: pydantic-settings JSON-decodes a complex-typed value *inside* the environment source,
#: before any field validator runs, so without :class:`~pydantic_settings.NoDecode` the
#: comma-separated form ``.env.example`` documents raises ``SettingsError`` at load time and
#: the ``mode="before"`` validators below never see it. With it, both documented forms — a
#: comma-separated string and a JSON array — are parsed by :func:`_parse_string_list`.
_StringList = Annotated[list[str], NoDecode]


def _parse_string_list(value: object) -> object:
    """Parse a configured list-of-strings setting.

    Args:
        value: The value as configured. A string is parsed; anything else — a list supplied
            directly to the constructor, or a default — is returned untouched.

    Returns:
        The parsed list, or ``value`` unchanged when it is not a string.

    Raises:
        ValueError: If the value opens like a JSON array but does not parse as one.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            message = f"expected a comma-separated list or a JSON array, got {value!r}"
            raise ValueError(message) from error
    return [item.strip() for item in text.split(",") if item.strip()]


class AppEnv(StrEnum):
    """Deployment environment the process is running in."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


class LogFormat(StrEnum):
    """Rendering of structured log records."""

    JSON = "json"
    TEXT = "text"


class EurLexDiscoveryDate(StrEnum):
    """Which CELLAR date the EU connector's discovery window bounds.

    ``modification`` is the incremental route: the window bounds the repository's own last
    modification timestamp, which is what makes a weekly run pick up newly published *and*
    newly corrected decisions, and what the ``ingest_checkpoint`` timestamp means.

    ``document`` bounds the date of the decision itself. It exists for backfilling a
    historical period — "every judgment delivered in 2024" — and deliberately leaves the
    incremental checkpoint alone, because a backfill says nothing about what has changed
    since.
    """

    MODIFICATION = "modification"
    DOCUMENT = "document"


class Settings(BaseSettings):
    """Validated application configuration.

    Attributes are grouped by concern: application, database, HTTP API, outbound HTTP
    client, data files and logging. Defaults are development-safe; anything that would be
    unsafe in production is validated in :meth:`_check_production_safety`.
    """

    model_config = SettingsConfigDict(
        env_prefix="PLT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- Application ----------------------------------------------------------------
    app_name: str = Field(
        default="Pesticide Litigation Tracker",
        description="Human-readable application name, used in logs and the User-Agent.",
    )
    app_env: AppEnv = Field(
        default=AppEnv.DEVELOPMENT,
        description="Deployment environment: development, testing or production.",
    )
    debug: bool = Field(
        default=False,
        description="Flask debug mode. Never enable in production.",
    )
    secret_key: SecretStr = Field(
        default=SecretStr(_PLACEHOLDER_SECRET_KEY),
        description="Flask secret key. Must be overridden outside development.",
    )

    # -- Database -------------------------------------------------------------------
    database_url: str = Field(
        default="sqlite+pysqlite:///./plt.db",
        description="SQLAlchemy database URL. SQLite in development, PostgreSQL in production.",
    )
    database_echo: bool = Field(
        default=False,
        description="Echo emitted SQL to the logger. Debugging aid only.",
    )
    database_pool_size: Annotated[int, Field(ge=1, le=100)] = Field(
        default=5,
        description="Connection pool size. Ignored by SQLite.",
    )
    database_pool_timeout_seconds: Annotated[int, Field(ge=1, le=300)] = Field(
        default=30,
        description="Seconds to wait for a pooled connection before failing.",
    )

    # -- HTTP API -------------------------------------------------------------------
    api_prefix: str = Field(
        default="/api",
        description="Base path every API blueprint is mounted under.",
    )
    cors_allowed_origins: _StringList = Field(
        default_factory=lambda: ["http://localhost:5173"],
        description="Comma-separated list of origins allowed to call the API.",
    )
    page_size_default: Annotated[int, Field(ge=1, le=1000)] = Field(
        default=20,
        description="Default page size for paginated list endpoints.",
    )
    page_size_max: Annotated[int, Field(ge=1, le=1000)] = Field(
        default=100,
        description="Hard upper bound on client-requested page size.",
    )
    latest_limit_default: Annotated[int, Field(ge=1, le=200)] = Field(
        default=20,
        description="Default number of cases returned by /api/cases/latest.",
    )
    latest_limit_max: Annotated[int, Field(ge=1, le=200)] = Field(
        default=50,
        description="Hard upper bound on the /api/cases/latest limit parameter.",
    )
    rate_limit_enabled: bool = Field(
        default=True,
        description="Master switch for API rate limiting. Disabled in tests.",
    )
    rate_limit_default: str = Field(
        default="120 per minute",
        description="Default rate limit applied to every API endpoint.",
    )
    rate_limit_export: str = Field(
        default="10 per hour",
        description="Stricter rate limit for the expensive /api/cases/export endpoint.",
    )
    rate_limit_storage_uri: str = Field(
        default="memory://",
        description="Rate limiter storage backend. Use redis://... for multi-process deploys.",
    )
    rate_limit_review: str = Field(
        default="30 per minute",
        description="Rate limit for the authenticated /api/reviews endpoints.",
    )

    # -- Review queue ---------------------------------------------------------------
    review_api_token: SecretStr | None = Field(
        default=None,
        description=(
            "Bearer token authenticating the review queue endpoints. Unset - the default - "
            "leaves /api/reviews disabled, because the queue exposes cases a rejection has "
            "unpublished and lets a caller unpublish more."
        ),
    )
    review_page_size_default: Annotated[int, Field(ge=1, le=1000)] = Field(
        default=20,
        description="Default page size for the review queue listing.",
    )

    # -- Outbound HTTP (source connectors) ------------------------------------------
    http_contact_email: str = Field(
        default="plt@wur.nl",
        description="Contact address advertised in the User-Agent, per architecture 2.5.",
    )
    http_user_agent_template: str = Field(
        default="PLT/{version} (+{repository}; {contact})",
        description="User-Agent template. Placeholders: version, repository, contact.",
    )
    http_repository_url: str = Field(
        default="https://github.com/IdseVal/PLT",
        description="Project URL advertised in the User-Agent.",
    )
    http_timeout_seconds: Annotated[float, Field(gt=0, le=600)] = Field(
        default=30.0,
        description="Per-request timeout for outbound source requests.",
    )
    http_max_retries: Annotated[int, Field(ge=0, le=10)] = Field(
        default=5,
        description="Retries on 429 and 5xx responses before giving up on a document.",
    )
    http_backoff_initial_seconds: Annotated[float, Field(gt=0, le=60)] = Field(
        default=1.0,
        description="First retry delay; doubled with jitter on each subsequent attempt.",
    )
    http_backoff_max_seconds: Annotated[float, Field(gt=0, le=600)] = Field(
        default=60.0,
        description="Ceiling on the exponential backoff delay the client computes itself.",
    )
    http_retry_after_max_seconds: Annotated[float, Field(gt=0, le=86400)] = Field(
        default=900.0,
        description=(
            "Longest Retry-After a run will wait out. The header is always obeyed as sent, "
            "never shortened; a source asking for longer than this ends the run instead, "
            "leaving the checkpoint for the next scheduled one."
        ),
    )
    http_requests_per_second: Annotated[float, Field(gt=0, le=100)] = Field(
        default=2.0,
        description="Politeness throttle applied to every source endpoint.",
    )

    # -- Pipeline -------------------------------------------------------------------
    pipeline_batch_size: Annotated[int, Field(ge=1, le=1000)] = Field(
        default=50,
        description="Documents processed and committed per batch; keeps memory flat.",
    )
    pipeline_page_size: Annotated[int, Field(ge=1, le=1000)] = Field(
        default=100,
        description="Result-page size requested from source endpoints during discovery.",
    )
    pipeline_report_dir: Path = Field(
        default=_REPO_ROOT / "reports",
        description="Directory a --dry-run match report is written to. Git-ignored.",
    )

    # -- Source endpoints -----------------------------------------------------------
    rechtspraak_search_url: str = Field(
        default="https://data.rechtspraak.nl/uitspraken/zoeken",
        description="Rechtspraak.nl Atom search endpoint (NL).",
    )
    rechtspraak_content_url: str = Field(
        default="https://data.rechtspraak.nl/uitspraken/content",
        description="Rechtspraak.nl document endpoint, queried with ?id=<ECLI> (NL).",
    )
    rechtspraak_vocabulary_url: str = Field(
        default="https://data.rechtspraak.nl/Waardelijst",
        description="Rechtspraak.nl controlled vocabularies used to seed reference tables.",
    )
    rechtspraak_documents_only: bool = Field(
        default=True,
        description=(
            "Restrict Rechtspraak discovery to ECLIs that carry text (return=DOC). The "
            "excluded registrations have no summary and no body, so they cost roughly three "
            "requests for every one that can match; disable only to mirror the bare register."
        ),
    )
    eurlex_sparql_url: str = Field(
        default="https://publications.europa.eu/webapi/rdf/sparql",
        description="CELLAR SPARQL endpoint used to enumerate EU case law.",
    )
    eurlex_cellar_base_url: str = Field(
        default="http://publications.europa.eu/resource/celex",
        description="CELLAR REST base; a CELEX number is appended to fetch a notice.",
    )
    eurlex_max_results: Annotated[int, Field(ge=1, le=1_000_000)] = Field(
        default=10_000,
        description=(
            "Results a single CELLAR search returns at most, capped by the Publications "
            "Office since 1 January 2026. Discovery keeps every date window under this."
        ),
    )
    eurlex_window_days: Annotated[int, Field(ge=1, le=3650)] = Field(
        default=30,
        description="Initial width of a CELLAR discovery date window, halved when it hits the cap.",
    )
    eurlex_min_window_seconds: Annotated[int, Field(ge=1, le=86_400 * 366)] = Field(
        default=3600,
        description=(
            "Narrowest a discovery window is split to. A window this small that still hits "
            "the cap is processed anyway and logged, rather than splitting forever."
        ),
    )
    eurlex_languages: _StringList = Field(
        default_factory=lambda: ["eng"],
        description=(
            "Manifestation languages to retrieve per case, in order of preference, as "
            "ISO 639-3 codes. A case with none of them falls back to its procedural "
            "language. Each retrieved language becomes its own case_document row."
        ),
    )
    eurlex_resource_types: _StringList = Field(
        default_factory=lambda: ["JUDG", "ORDER", "OPIN_AG", "JUDG_EXTRACT"],
        description=(
            "CDM resource-type codes discovery enumerates: the decisions themselves. "
            "Leave the Official Journal notices (INFO_JUDICIAL) and editorial summaries "
            "(SUM_JUR, ABSTRACT_JUR) out, or every case is discovered several times over."
        ),
    )
    eurlex_discovery_date: EurLexDiscoveryDate = Field(
        default=EurLexDiscoveryDate.MODIFICATION,
        description=(
            "Which CELLAR date the discovery window bounds. 'modification' is the "
            "incremental route the weekly job needs; 'document' walks the date of the "
            "decision itself, for backfilling a historical period, and never advances the "
            "incremental checkpoint."
        ),
    )

    # -- Data files -----------------------------------------------------------------
    data_dir: Path = Field(
        default=_REPO_ROOT / "data",
        description="Root of the curated reference data directory.",
    )
    keywords_dir: Path = Field(
        default=_REPO_ROOT / "data" / "keywords",
        description="Per-jurisdiction keyword filter lists (architecture section 1).",
    )

    # -- Logging --------------------------------------------------------------------
    log_level: str = Field(
        default="INFO",
        description="Root log level: DEBUG, INFO, WARNING, ERROR or CRITICAL.",
    )
    log_format: LogFormat = Field(
        default=LogFormat.JSON,
        description="Log record rendering: json for deployments, text for local work.",
    )

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string as well as a JSON list for CORS origins."""
        return _parse_string_list(value)

    @field_validator("eurlex_languages", "eurlex_resource_types", mode="before")
    @classmethod
    def _split_codes(cls, value: object) -> object:
        """Accept a comma-separated string as well as a JSON list for the CELLAR code lists."""
        return _parse_string_list(value)

    @field_validator("eurlex_languages", "eurlex_resource_types")
    @classmethod
    def _validate_codes(cls, value: list[str]) -> list[str]:
        """Reject a controlled-vocabulary code that is not one.

        These codes are interpolated into SPARQL vocabulary URIs, so the character set is
        checked here, at the configuration boundary, rather than trusted downstream. Nothing
        outside ``A-Z``, ``a-z``, ``0-9`` and ``_`` can reach a query.

        Args:
            value: The codes as configured.

        Returns:
            The codes, upper-cased and de-duplicated, order preserved.

        Raises:
            ValueError: If the list is empty or a code carries anything else.
        """
        if not value:
            message = "at least one code is required"
            raise ValueError(message)
        seen: dict[str, None] = {}
        for code in value:
            if not _VOCABULARY_CODE.fullmatch(code):
                message = (
                    f"{code!r} is not a CELLAR vocabulary code; expected letters, digits and "
                    "underscores only"
                )
                raise ValueError(message)
            seen.setdefault(code.upper(), None)
        return list(seen)

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        """Upper-case the log level and reject values ``logging`` cannot resolve."""
        if not isinstance(value, str):
            return value
        level = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level not in allowed:
            message = f"log_level must be one of {sorted(allowed)}, got {value!r}"
            raise ValueError(message)
        return level

    @field_validator("api_prefix")
    @classmethod
    def _validate_api_prefix(cls, value: str) -> str:
        """Require a leading slash and no trailing slash so blueprint mounts concatenate."""
        if not value.startswith("/"):
            message = f"api_prefix must start with '/', got {value!r}"
            raise ValueError(message)
        return value.rstrip("/") or "/"

    @model_validator(mode="after")
    def _check_bounds(self) -> Settings:
        """Keep the default page size within its own maximum."""
        if self.page_size_default > self.page_size_max:
            message = "page_size_default may not exceed page_size_max"
            raise ValueError(message)
        if self.latest_limit_default > self.latest_limit_max:
            message = "latest_limit_default may not exceed latest_limit_max"
            raise ValueError(message)
        if self.http_backoff_initial_seconds > self.http_backoff_max_seconds:
            message = "http_backoff_initial_seconds may not exceed http_backoff_max_seconds"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _check_production_safety(self) -> Settings:
        """Refuse to start production with a placeholder secret or debug enabled."""
        if self.app_env is not AppEnv.PRODUCTION:
            return self
        if self.secret_key.get_secret_value() == _PLACEHOLDER_SECRET_KEY:
            message = "PLT_SECRET_KEY must be set to a generated value when PLT_APP_ENV=production"
            raise ValueError(message)
        if self.debug:
            message = "PLT_DEBUG must be false when PLT_APP_ENV=production"
            raise ValueError(message)
        return self

    @property
    def is_production(self) -> bool:
        """Return whether the process runs in the production environment."""
        return self.app_env is AppEnv.PRODUCTION

    @property
    def is_testing(self) -> bool:
        """Return whether the process runs under the test environment."""
        return self.app_env is AppEnv.TESTING

    def keyword_list_path(self, jurisdiction_code: str) -> Path:
        """Return the keyword list path for a jurisdiction code.

        Args:
            jurisdiction_code: Jurisdiction code such as ``NL`` or ``EU``. Case-insensitive.

        Returns:
            Path to ``<keywords_dir>/<code lowercased>.json``. The file is not required to
            exist; callers report a missing list as an onboarding error.

        Raises:
            ValueError: If the code is not two ASCII letters. Guards against a caller
                turning user input into a path traversal.
        """
        code = jurisdiction_code.strip()
        if len(code) != 2 or not code.isascii() or not code.isalpha():
            message = f"jurisdiction_code must be two ASCII letters, got {jurisdiction_code!r}"
            raise ValueError(message)
        return self.keywords_dir / f"{code.lower()}.json"

    def user_agent(self, version: str) -> str:
        """Render the outbound ``User-Agent`` header.

        Args:
            version: Package version to advertise.

        Returns:
            A descriptive User-Agent naming the project and a contact address, as required
            by ``docs/architecture.md`` section 2.5.
        """
        return self.http_user_agent_template.format(
            version=version,
            repository=self.http_repository_url,
            contact=self.http_contact_email,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    The result is cached, so the environment is read and validated once. Tests that need
    different values should call ``get_settings.cache_clear()`` after patching the
    environment, or construct :class:`Settings` directly with overrides.

    Returns:
        The validated :class:`Settings` instance.
    """
    return Settings()
