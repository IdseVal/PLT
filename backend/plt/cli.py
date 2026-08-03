"""Command line entry points.

Two equivalent routes into the same commands, per ``docs/architecture.md`` sections 1 and 7:

* ``flask --app plt.app plt <command>`` - the group is attached to the Flask CLI;
* ``python -m plt.cli <command>`` / ``plt <command>`` - the same group standalone, so a
  server cron can call the identical code path as the scheduled workflow.

The commands themselves (``ingest``, ``init-db``, ...) belong to the pipeline and database
issues; this module provides the group they attach to.
"""

from __future__ import annotations

import sys

import click

from plt.config import get_settings
from plt.utils.logging import configure_logging, get_logger

__all__ = ["main", "plt_cli"]

log = get_logger(__name__)


@click.group(name="plt")
@click.version_option(package_name="plt")
def plt_cli() -> None:
    """PLT maintenance and ingestion commands."""
    configure_logging(get_settings())


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv: Argument vector, excluding the program name. Defaults to ``sys.argv[1:]``.

    Returns:
        A process exit code: ``0`` on success, ``130`` when interrupted with Ctrl+C,
        ``1`` on an unhandled failure.
    """
    try:
        plt_cli.main(args=argv, standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.exceptions.Abort:
        return 130
    except KeyboardInterrupt:
        log.warning("interrupted; shutting down cleanly")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
