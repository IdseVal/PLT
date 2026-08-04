"""The HTTP client every connector fetches through.

Politeness is a property of the pipeline, not of a jurisdiction (``docs/architecture.md``
rule 2.5), so it lives here rather than in each connector: one throttle, one retry policy,
one ``User-Agent``. A connector that composes its own requests with :mod:`httpx` directly
bypasses all three, which is exactly the leak this module exists to prevent.

What it guarantees, all of it from :class:`plt.config.Settings` and none of it hard-coded:

* **Request rate.** At most ``http_requests_per_second`` requests leave the process per
  client, spaced evenly rather than burst-then-idle.
* **Backoff.** ``429`` and ``5xx`` are retried up to ``http_max_retries`` times with an
  exponential delay, capped at ``http_backoff_max_seconds`` and jittered so that two
  jurisdictions running in parallel do not resynchronise onto the same retry schedule. A
  ``Retry-After`` header wins over the computed delay, because the server knows better.
* **Identification.** Every request carries the descriptive ``User-Agent`` from
  :meth:`plt.config.Settings.user_agent`, naming the project, its repository and a contact
  address, so an operator whose endpoint we are inconveniencing can reach a human.
* **Interruptibility.** Backoff sleeps in short slices and checks the caller's stop
  predicate between them, so ``Ctrl+C`` during a 60-second backoff does not wait it out.

These are public research endpoints belonging to courts. The defaults are deliberately
conservative; raising them is a decision, not a tweak.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, Final, Self

import httpx

from plt import __version__
from plt.config import Settings, get_settings
from plt.pipeline.base import ConnectorError, DocumentUnavailableError, SourceUnavailableError
from plt.utils.logging import get_logger

__all__ = [
    "PoliteClient",
    "RateLimiter",
    "build_headers",
]

log = get_logger(__name__)

#: Status codes worth retrying: the source asked us to slow down, or it is having a bad day.
_RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({408, 425, 429, 500, 502, 503, 504})

#: Statuses that mean "this document is not available", as opposed to "the source is down".
_MISSING_STATUS: Final[frozenset[int]] = frozenset({404, 410})

#: Longest uninterrupted sleep. A backoff is served as a sequence of these so that a stop
#: request is noticed within a fraction of a second rather than at the end of a minute.
_SLEEP_SLICE: Final[float] = 0.25


def build_headers(settings: Settings, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build the default headers for outbound source requests.

    Args:
        settings: Settings supplying the ``User-Agent`` template, repository URL and contact
            address.
        extra: Additional headers, e.g. an ``Accept`` a particular endpoint needs.

    Returns:
        The header mapping, with the descriptive ``User-Agent`` always present.
    """
    headers = {"User-Agent": settings.user_agent(__version__)}
    if extra:
        headers.update(extra)
    return headers


class RateLimiter:
    """An evenly spaced throttle: at most ``requests_per_second`` acquisitions per second.

    Spacing rather than bursting is the point. A token bucket would let a run fire fifty
    requests in the first second of every minute, which is precisely the shape of traffic
    that gets a research crawler blocked.

    The clock is injectable so tests can assert on the spacing without spending real time.
    """

    def __init__(
        self,
        requests_per_second: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Configure the throttle.

        Args:
            requests_per_second: Maximum sustained request rate.
            monotonic: Clock used to measure the interval; injectable for tests.
            sleep: Sleep function; injectable for tests.

        Raises:
            ValueError: If the rate is not positive.
        """
        if requests_per_second <= 0:
            message = f"requests_per_second must be positive, got {requests_per_second}"
            raise ValueError(message)
        self._interval = 1.0 / requests_per_second
        self._monotonic = monotonic
        self._sleep = sleep
        self._next_allowed: float | None = None

    @property
    def interval(self) -> float:
        """Return the minimum interval between two requests, in seconds."""
        return self._interval

    def acquire(self) -> None:
        """Block until the next request may be sent."""
        now = self._monotonic()
        if self._next_allowed is not None and now < self._next_allowed:
            self._sleep(self._next_allowed - now)
            now = self._monotonic()
        self._next_allowed = now + self._interval


class PoliteClient:
    """An :mod:`httpx` client that throttles, retries, backs off and identifies itself.

    Construct one per connector and close it in :meth:`~plt.pipeline.base.SourceConnector.close`,
    or use it as a context manager. It is not thread-safe: the throttle is per instance, and
    the pipeline is single-threaded by design so that the request rate a source sees is the
    configured one.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        headers: Mapping[str, str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        should_stop: Callable[[], bool] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        """Build the client.

        Args:
            settings: Settings supplying the rate, timeout, retry budget and ``User-Agent``.
                Defaults to the process-wide settings.
            client: An existing :class:`httpx.Client` to send through. Ownership passes to
                this object, which closes it.
            transport: Transport to build the client on, e.g. an
                :class:`httpx.MockTransport` in tests. Ignored when ``client`` is given.
            headers: Extra default headers, e.g. an ``Accept``.
            sleep: Sleep function; injectable so tests never spend real time backing off.
            monotonic: Clock used by the throttle; injectable for tests.
            should_stop: Predicate the client consults before a request and between backoff
                slices. The runner passes its interruption flag, so ``Ctrl+C`` is not
                swallowed by a long backoff.
            rng: Random source for backoff jitter; injectable so a test can pin the delays.
        """
        self._settings = settings if settings is not None else get_settings()
        self._sleep = sleep
        self._should_stop = should_stop if should_stop is not None else _never
        self._rng = rng if rng is not None else random.Random()  # noqa: S311 - jitter, not crypto
        self._limiter = RateLimiter(
            self._settings.http_requests_per_second,
            monotonic=monotonic,
            sleep=self._interruptible_sleep,
        )
        self._owns_client = client is None
        self._client = client if client is not None else self._build_client(transport, headers)

    def _build_client(
        self,
        transport: httpx.BaseTransport | None,
        headers: Mapping[str, str] | None,
    ) -> httpx.Client:
        """Build the underlying client from settings.

        Args:
            transport: Transport to use, or ``None`` for the default network transport.
            headers: Extra default headers.

        Returns:
            A configured :class:`httpx.Client`.
        """
        return httpx.Client(
            timeout=self._settings.http_timeout_seconds,
            headers=build_headers(self._settings, headers),
            transport=transport,
            follow_redirects=True,
        )

    @property
    def settings(self) -> Settings:
        """Return the settings this client was configured from."""
        return self._settings

    @property
    def user_agent(self) -> str:
        """Return the ``User-Agent`` sent with every request."""
        return str(self._client.headers.get("User-Agent", ""))

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Send a throttled, retrying ``GET``.

        Args:
            url: Absolute URL to request.
            params: Query parameters. Passed to :mod:`httpx`, which encodes them, so a value
                taken from a source payload cannot inject further parameters.
            headers: Per-request headers, merged over the defaults.

        Returns:
            The successful response.

        Raises:
            DocumentUnavailableError: On a ``4xx`` that is not worth retrying.
            SourceUnavailableError: When the retry budget is exhausted or the run was asked
                to stop.
        """
        return self.request("GET", url, params=params, headers=headers)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        content: bytes | str | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        """Send a request, respecting the rate limit and retrying what is worth retrying.

        Args:
            method: HTTP method.
            url: Absolute URL to request.
            params: Query parameters.
            headers: Per-request headers, merged over the defaults.
            content: Raw request body, e.g. a SPARQL query.
            data: Form body.

        Returns:
            The successful response.

        Raises:
            DocumentUnavailableError: On a ``4xx`` that is not worth retrying — a missing
                document must not abort a run over thousands of others.
            SourceUnavailableError: When the retry budget is exhausted, the transport keeps
                failing, or the run was asked to stop mid-retry.
        """
        attempts = self._settings.http_max_retries + 1
        last_reason = "no attempt was made"
        for attempt in range(attempts):
            if self._should_stop():
                message = f"stop requested before {method} {url}"
                raise SourceUnavailableError(message)
            self._limiter.acquire()
            try:
                response = self._client.request(
                    method, url, params=params, headers=headers, content=content, data=data
                )
            except httpx.HTTPError as error:
                last_reason = f"{type(error).__name__}: {error}"
                retry_after = None
            else:
                if response.status_code < httpx.codes.BAD_REQUEST:
                    return response
                last_reason = f"HTTP {response.status_code}"
                if response.status_code not in _RETRYABLE_STATUS:
                    raise self._client_error(method, url, response)
                retry_after = _retry_after_seconds(response)
            if attempt + 1 >= attempts:
                break
            delay = self._backoff_delay(attempt, retry_after)
            log.warning(
                "source request failed; backing off",
                extra={
                    "context": {
                        "method": method,
                        "url": url,
                        "attempt": attempt + 1,
                        "of": attempts,
                        "reason": last_reason,
                        "delay_seconds": round(delay, 3),
                    }
                },
            )
            self._interruptible_sleep(delay)
        message = f"{method} {url} failed after {attempts} attempt(s): {last_reason}"
        raise SourceUnavailableError(message)

    def _client_error(self, method: str, url: str, response: httpx.Response) -> ConnectorError:
        """Turn a non-retryable response into the exception the runner expects.

        Args:
            method: HTTP method of the request.
            url: URL requested.
            response: The response received.

        Returns:
            A :class:`~plt.pipeline.base.DocumentUnavailableError`, which the runner logs and
            skips. Even a ``400`` is scoped to the document: one malformed identifier in a
            feed must not end a run over the rest of the window.
        """
        missing = response.status_code in _MISSING_STATUS
        detail = "not published at the source" if missing else "rejected by the source"
        message = f"{method} {url}: HTTP {response.status_code}, {detail}"
        return DocumentUnavailableError(message)

    def _backoff_delay(self, attempt: int, retry_after: float | None) -> float:
        """Compute the delay before the next attempt.

        Equal jitter: half the exponential delay, plus a random share of the other half. It
        keeps the retries spread out without ever collapsing to zero, which full jitter can.

        Args:
            attempt: Zero-based number of the attempt that just failed.
            retry_after: Seconds the server asked for, if it said.

        Returns:
            The delay in seconds, never above ``http_backoff_max_seconds``.
        """
        ceiling: float = self._settings.http_backoff_max_seconds
        exponential: float = min(self._settings.http_backoff_initial_seconds * 2**attempt, ceiling)
        jittered: float = exponential / 2 + self._rng.uniform(0.0, exponential / 2)
        if retry_after is not None:
            return min(max(retry_after, jittered), ceiling)
        return jittered

    def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep, but notice a stop request while doing so.

        Args:
            seconds: How long to wait in total.
        """
        remaining = seconds
        while remaining > 0:
            if self._should_stop():
                return
            slice_length = min(_SLEEP_SLICE, remaining)
            self._sleep(slice_length)
            remaining -= slice_length

    def close(self) -> None:
        """Close the underlying client, unless it was passed in by the caller."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        """Return the client, so it can be used as a context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the client on leaving the context."""
        del exc_type, exc, traceback
        self.close()


def _never() -> bool:
    """Return ``False``: the default stop predicate, which never asks to stop."""
    return False


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Read a ``Retry-After`` header, in either of its two forms.

    Args:
        response: The response to read the header from.

    Returns:
        The delay in seconds, or ``None`` when the header is absent or unparseable. A header
        in the past yields ``0.0`` rather than a negative delay.
    """
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:  # pragma: no cover - RFC 7231 requires an offset
        return None
    return max(0.0, (moment - datetime.now(UTC)).total_seconds())
