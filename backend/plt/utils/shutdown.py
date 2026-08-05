"""Graceful-shutdown flag shared by every long-running job.

``docs/architecture.md`` rule 2.2 makes interruptibility a property of the whole code base
rather than of the ingestion pipeline alone: a long loop finishes the item in flight, writes
its position and exits cleanly. The ingestion runner and the weekly digest are both such
loops, so the flag they cooperate with lives here rather than being written twice.

Trapping the signal beats letting ``KeyboardInterrupt`` propagate: the interrupt would land
in the middle of a batch, roll it back and throw away work already done. With a flag, the
item in flight finishes, its batch commits, and what was committed is exactly what the job
records. A second signal restores the previous handler and re-raises, so an impatient
operator can still force the issue.
"""

from __future__ import annotations

import signal
import threading
from types import FrameType, TracebackType
from typing import TYPE_CHECKING, Any, Self

from plt.utils.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["StopRequest"]

log = get_logger(__name__)


class StopRequest:
    """A shutdown flag, set by ``SIGINT``/``SIGTERM`` while a job is in progress.

    Used as a context manager, which installs the handlers on entry and restores the previous
    ones on exit. Handlers can only be installed from the main thread; anywhere else the
    guard degrades to a no-op and the caller's own ``KeyboardInterrupt`` handling applies.
    """

    def __init__(self, signals: Sequence[int] | None = None) -> None:
        """Prepare the guard.

        Args:
            signals: Signal numbers to trap. Defaults to ``SIGINT`` and, where the platform
                has it, ``SIGTERM`` — what a container runtime or a cancelled CI job sends.
        """
        if signals is None:
            signals = [signal.SIGINT, *([signal.SIGTERM] if hasattr(signal, "SIGTERM") else [])]
        self._signals = tuple(signals)
        self._previous: dict[int, Any] = {}
        self._requested = False

    @property
    def requested(self) -> bool:
        """Return whether a shutdown has been requested."""
        return self._requested

    def __call__(self) -> bool:
        """Return whether a shutdown has been requested, as a predicate.

        Passed to :class:`plt.pipeline.http.PoliteClient`, so a backoff sleep is abandoned as
        soon as the run is asked to stop.
        """
        return self._requested

    def request(self, reason: str) -> None:
        """Request a graceful shutdown.

        Args:
            reason: What asked for it, for the log line.
        """
        if not self._requested:
            log.warning(
                "shutdown requested; finishing the item in flight",
                extra={"context": {"reason": reason}},
            )
        self._requested = True

    def _handle(self, signal_number: int, frame: FrameType | None) -> None:
        """Handle a trapped signal.

        The first requests a graceful stop; a second restores the previous handler and
        re-raises, so ``Ctrl+C`` twice still stops the process at once.

        Args:
            signal_number: The signal received.
            frame: The interrupted stack frame, unused.
        """
        del frame
        if self._requested:
            self._restore()
            signal.raise_signal(signal_number)
            return
        self.request(f"signal {signal.Signals(signal_number).name}")

    def _restore(self) -> None:
        """Put the previous signal handlers back."""
        for number, handler in self._previous.items():
            signal.signal(number, handler)
        self._previous.clear()

    def __enter__(self) -> Self:
        """Install the handlers, if this is the main thread."""
        if threading.current_thread() is not threading.main_thread():
            return self
        for number in self._signals:
            try:
                self._previous[number] = signal.signal(number, self._handle)
            except (ValueError, OSError):  # pragma: no cover - platform dependent
                log.debug("could not trap signal", extra={"context": {"signal": number}})
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Restore the previous handlers."""
        del exc_type, exc, traceback
        self._restore()
