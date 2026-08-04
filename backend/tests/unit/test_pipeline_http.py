"""Politeness towards the sources, which are public research endpoints.

Every value here comes from :class:`plt.config.Settings` — the request rate, the retry
budget, the backoff bounds, the contact address in the ``User-Agent``. The tests drive an
:class:`httpx.MockTransport`, so nothing leaves the process, and inject the clock and the
sleep function, so a test of a sixty-second backoff takes no time at all.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from itertools import pairwise

import httpx
import pytest

from plt import __version__
from plt.pipeline.base import DocumentUnavailableError, SourceUnavailableError
from plt.pipeline.http import PoliteClient, RateLimiter, build_headers
from tests.conftest import build_settings

URL = "https://source.invalid/document"


class Clock:
    """A monotonic clock that only moves when something sleeps."""

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        """Return the current instant."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance the clock and record the wait."""
        self.slept.append(seconds)
        self.now += seconds


def responder(
    *statuses: int,
    headers: dict[str, str] | None = None,
    at: list[float] | None = None,
    clock: Clock | None = None,
) -> Callable[..., httpx.Response]:
    """Build a transport handler returning the given statuses in turn.

    Args:
        *statuses: Statuses to return, in order; 200 once they run out.
        headers: Response headers.
        at: List the instant of each request is appended to, for the timing assertions.
        clock: Clock those instants are read from.

    Returns:
        The handler.
    """
    remaining = list(statuses)

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        if at is not None and clock is not None:
            at.append(clock.now)
        status = remaining.pop(0) if remaining else 200
        return httpx.Response(status, text="payload", headers=headers or {})

    return handle


def gaps(instants: list[float]) -> list[float]:
    """Return the waits between consecutive requests."""
    return [round(later - earlier, 6) for earlier, later in pairwise(instants)]


def client(
    handler: Callable[..., httpx.Response],
    clock: Clock,
    **overrides: object,
) -> PoliteClient:
    """Build a polite client over a mock transport and a fake clock."""
    settings = build_settings(**overrides)
    return PoliteClient(
        settings,
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        # A seeded generator, so the jitter bounds below are reproducible. Not crypto.
        rng=random.Random(0),  # noqa: S311
    )


def test_the_user_agent_names_the_project_and_a_contact_address() -> None:
    settings = build_settings(http_contact_email="plt@wur.nl")

    agent = build_headers(settings)["User-Agent"]

    assert __version__ in agent
    assert "plt@wur.nl" in agent
    assert settings.http_repository_url in agent


def test_every_request_carries_the_user_agent() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["User-Agent"])
        return httpx.Response(200, text="ok")

    with client(handle, Clock()) as polite:
        polite.get(URL)

    assert seen == [polite.user_agent]
    assert "wur.nl" in seen[0]


def test_requests_are_spaced_by_the_configured_rate() -> None:
    clock = Clock()
    sent: list[float] = []

    with client(responder(at=sent, clock=clock), clock, http_requests_per_second=2.0) as polite:
        polite.get(URL)
        polite.get(URL)
        polite.get(URL)

    # Evenly spaced rather than bursting: two per second means one every half second,
    # which is the shape of traffic a research endpoint tolerates.
    assert gaps(sent) == [0.5, 0.5]


def test_the_rate_limiter_refuses_a_nonsensical_rate() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        RateLimiter(0)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_a_retryable_status_is_retried(status: int) -> None:
    clock = Clock()

    with client(responder(status), clock) as polite:
        response = polite.get(URL)

    assert response.status_code == 200
    assert len(clock.slept) >= 1


def test_the_backoff_grows_exponentially_and_is_jittered() -> None:
    clock = Clock()

    sent: list[float] = []
    with client(
        responder(503, 503, 503, at=sent, clock=clock),
        clock,
        http_requests_per_second=100.0,
        http_backoff_initial_seconds=1.0,
        http_backoff_max_seconds=60.0,
    ) as polite:
        polite.get(URL)

    waits = gaps(sent)
    assert len(waits) == 3
    # Equal jitter: each wait sits in [half, full] of the exponential delay, and never
    # collapses to zero, so retries stay spread out without stampeding.
    assert 0.5 <= waits[0] <= 1.01
    assert 1.0 <= waits[1] <= 2.01
    assert 2.0 <= waits[2] <= 4.01
    assert len({round(wait, 6) for wait in waits}) == 3


def test_the_backoff_is_capped() -> None:
    clock = Clock()

    with client(
        responder(503, 503, 503),
        clock,
        http_requests_per_second=100.0,
        http_backoff_initial_seconds=10.0,
        http_backoff_max_seconds=12.0,
    ) as polite:
        polite.get(URL)

    assert clock.now <= 3 * 12.0 + 1


def test_a_retry_after_header_wins_over_the_computed_delay() -> None:
    """The server knows better than the client's exponential guess."""
    clock = Clock()
    sent: list[float] = []

    with client(
        responder(429, headers={"Retry-After": "17"}, at=sent, clock=clock),
        clock,
        http_requests_per_second=100.0,
    ) as polite:
        polite.get(URL)

    assert gaps(sent)[0] == pytest.approx(17.0, abs=0.02)


def test_the_retry_budget_is_finite() -> None:
    clock = Clock()

    with (
        client(responder(*([503] * 10)), clock, http_max_retries=2) as polite,
        pytest.raises(SourceUnavailableError, match="after 3 attempt"),
    ):
        polite.get(URL)


def test_a_transport_failure_is_retried_then_reported() -> None:
    clock = Clock()

    def handle(request: httpx.Request) -> httpx.Response:
        message = "connection reset"
        raise httpx.ConnectError(message, request=request)

    with (
        client(handle, clock, http_max_retries=1) as polite,
        pytest.raises(SourceUnavailableError, match="ConnectError"),
    ):
        polite.get(URL)


@pytest.mark.parametrize("status", [400, 404, 410])
def test_a_client_error_is_scoped_to_the_document(status: int) -> None:
    """One bad identifier in a feed must not end a run over the rest of the window."""
    with client(responder(status), Clock()) as polite, pytest.raises(DocumentUnavailableError):
        polite.get(URL)


def test_a_backoff_is_abandoned_when_the_run_is_asked_to_stop() -> None:
    clock = Clock()
    stopping = {"now": False}

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        stopping["now"] = True
        return httpx.Response(503)

    settings = build_settings(http_backoff_initial_seconds=60.0, http_backoff_max_seconds=60.0)
    polite = PoliteClient(
        settings,
        transport=httpx.MockTransport(handle),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        should_stop=lambda: stopping["now"],
    )

    with polite, pytest.raises(SourceUnavailableError, match="stop requested"):
        polite.get(URL)

    # Ctrl+C during a minute-long backoff is not waited out: the sleep is served in
    # slices and abandoned at the first check.
    assert sum(clock.slept) <= 0.25


def test_a_caller_supplied_client_is_left_open() -> None:
    inner = httpx.Client(transport=httpx.MockTransport(responder()))
    polite = PoliteClient(build_settings(), client=inner)

    polite.close()

    assert not inner.is_closed
    inner.close()
