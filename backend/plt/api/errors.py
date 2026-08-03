"""Uniform error envelope for the HTTP API.

``docs/architecture.md`` section 5 fixes one response shape for every failure::

    {"error": {"code": "...", "message": "...", "details": {...}}}

Handlers registered here guarantee that shape for application errors, for Werkzeug HTTP
errors (404, 405, 429, ...) and for anything unhandled. Internal failures are logged with
their traceback and answered with a generic message: no stack trace, SQL or database
identifier ever reaches a client.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Final

from flask import Flask
from werkzeug.exceptions import HTTPException

from plt.utils.logging import get_logger

__all__ = [
    "ApiError",
    "NotFoundError",
    "ValidationError",
    "error_response",
    "register_error_handlers",
]

log = get_logger(__name__)

#: Message returned for any unhandled exception; details stay in the logs.
_INTERNAL_MESSAGE: Final[str] = "An internal error occurred."

#: Response body type: the envelope is a JSON object.
ErrorEnvelope = dict[str, dict[str, Any]]


class ApiError(Exception):
    """Base class for errors that are safe to render to a client.

    Attributes:
        code: Stable, machine-readable error code, e.g. ``not_found``.
        message: Human-readable message. Must not contain SQL, paths or stack traces.
        status_code: HTTP status to answer with.
        details: Optional structured context, e.g. which parameter failed validation.
    """

    status_code: int = HTTPStatus.BAD_REQUEST
    code: str = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialise the error.

        Args:
            message: Client-safe description of what went wrong.
            code: Override for the class-level error code.
            status_code: Override for the class-level HTTP status.
            details: Optional structured context included in the envelope.
        """
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details: dict[str, Any] = details or {}

    def to_envelope(self) -> ErrorEnvelope:
        """Return this error as the contract's response envelope.

        Returns:
            The ``{"error": {...}}`` mapping, ready to be serialised as JSON.
        """
        return error_response(self.code, self.message, self.details)


class ValidationError(ApiError):
    """A query parameter or request body failed server-side validation."""

    status_code = HTTPStatus.BAD_REQUEST
    code = "validation_error"


class NotFoundError(ApiError):
    """The requested resource does not exist."""

    status_code = HTTPStatus.NOT_FOUND
    code = "not_found"


def error_response(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> ErrorEnvelope:
    """Build the uniform error envelope.

    Args:
        code: Machine-readable error code.
        message: Client-safe message.
        details: Optional structured context.

    Returns:
        The ``{"error": {"code": ..., "message": ..., "details": ...}}`` mapping.
    """
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_error_handlers(app: Flask) -> None:
    """Register the application-wide error handlers.

    Args:
        app: The Flask application to register on.
    """

    @app.errorhandler(ApiError)
    def _handle_api_error(error: ApiError) -> tuple[ErrorEnvelope, int]:
        log.info(
            "api error",
            extra={"error_code": error.code, "status": error.status_code},
        )
        return error.to_envelope(), error.status_code

    @app.errorhandler(HTTPException)
    def _handle_http_exception(error: HTTPException) -> tuple[ErrorEnvelope, int]:
        status = error.code or HTTPStatus.INTERNAL_SERVER_ERROR
        code = (error.name or "error").lower().replace(" ", "_")
        message = error.description or error.name or _INTERNAL_MESSAGE
        return error_response(code, message), status

    @app.errorhandler(Exception)
    def _handle_unexpected(error: Exception) -> tuple[ErrorEnvelope, int]:
        log.exception("unhandled exception", exc_info=error)
        return (
            error_response("internal_error", _INTERNAL_MESSAGE),
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )
