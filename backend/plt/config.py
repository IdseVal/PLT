"""Application settings, loaded from the environment.

``docs/architecture.md`` section 2.1 makes configuration external without exception: no URL,
path, credential, page size or rate limit may be hard-coded elsewhere in the code base.
Every setting below is overridable through an environment variable prefixed with ``PLT_``
(or an entry in a ``.env`` file), and every one of them is documented in ``.env.example``.

Read settings through :func:`get_settings`, which caches a single validated instance for the
process, rather than by instantiating :class:`Settings` directly.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Final

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "AppEnv",
    "LogFormat",
    "Settings",
    "get_settings",
]

#: Repository root, resolved from this file: ``<repo>/backend/plt/config.py``.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

#: Placeholder secret shipped in ``.env.example``; refused outside development.
_PLACEHOLDER_SECRET_KEY: Final[str] = "change-me-in-production"  # noqa: S105


class AppEnv(StrEnum):
    """Deployment environment the process is running in."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


class LogFormat(StrEnum):
    """Rendering of structured log records."""

    JSON = "json"
    TEXT = "text"


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
    cors_allowed_origins: list[str] = Field(
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
        description="Ceiling on the exponential backoff delay.",
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
    eurlex_sparql_url: str = Field(
        default="https://publications.europa.eu/webapi/rdf/sparql",
        description="CELLAR SPARQL endpoint used to enumerate EU case law.",
    )
    eurlex_cellar_base_url: str = Field(
        default="http://publications.europa.eu/resource/celex",
        description="CELLAR REST base; a CELEX number is appended to fetch a notice.",
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
        if isinstance(value, str) and not value.strip().startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

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
