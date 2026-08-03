"""Alembic environment.

The database URL comes from :class:`plt.config.Settings`, never from ``alembic.ini``, so
credentials stay in the environment. ``target_metadata`` is the shared metadata from
:mod:`plt.db.base`; importing :mod:`plt.db.models` registers every table on it before
autogenerate runs.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import plt.db.models  # noqa: F401 - imported for its side effect of registering models
from plt.config import get_settings
from plt.db.base import metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting to a database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection, closing it in every case."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                # Batch mode lets SQLite alter tables, keeping development and
                # PostgreSQL deployments on the same revisions.
                render_as_batch=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
