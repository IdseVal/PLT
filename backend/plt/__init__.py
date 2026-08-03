"""Pesticide Litigation Tracker backend package.

The package is laid out per ``docs/architecture.md`` section 1:

* :mod:`plt.config` - environment-driven settings, the only source of configuration.
* :mod:`plt.app` - the Flask application factory.
* :mod:`plt.extensions` - database session, CORS and rate limiter wiring.
* :mod:`plt.db` - declarative base, ORM models, session lifecycle and repositories.
* :mod:`plt.api` - HTTP blueprints, schemas and the uniform error envelope.
* :mod:`plt.pipeline` - ingestion runner, connectors and the pluggable filter chain.
* :mod:`plt.cli` - command line entry points.
* :mod:`plt.utils` - cross-cutting helpers such as structured logging.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
