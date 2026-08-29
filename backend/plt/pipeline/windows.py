"""Date windows a discovery walk is cut into, and the arithmetic that resizes them.

Every source this project reads is enumerated the same way: ask for the records modified
between two instants, and read what comes back. Neither source will hand over an unbounded
range in one answer, so both cut the range into windows — and both need the same two moves
when a window turns out to be the wrong size.

**Halving**, when a window holds more than one request can safely carry. CELLAR returns at
most 10,000 rows per search, so a window over that used to have a tail nothing could reach;
Rechtspraak serves at most 1,000 entries per request and orders them on a timestamp that is
not unique, so a window over that has to be *paged*, and paging over a non-unique order is
how the EU corpus silently lost a sixth of its cases. Different reasons, one move: halve the
window and ask again.

**Growing**, when a window holds almost nothing. A Dutch backfill starts in 1900 and the
first record is from 2013; a walk in fixed one-day steps would spend 41,000 requests
establishing that a century is empty. A window that comes back nearly empty is evidence the
next one can be wider, so the width is doubled up to a ceiling and the empty stretch is
crossed in a few dozen requests instead.

The two compose into a walk that finds its own width: it widens over sparse history, narrows
into a bulk re-publication that puts a hundred thousand records into a single afternoon, and
needs no operator to have guessed the right number for either.

Whether ``stop`` is part of the window is deliberately **not** decided here. Rechtspraak's
``modified`` bounds are both inclusive and resolve to the second, so its connector renders
``stop`` as the last instant it wants and starts the next window one second later; CELLAR's
are half-open, so its connector starts the next window at ``stop``. A window is a pair of
instants and the arithmetic for resizing it — the convention belongs to the source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = ["Window"]


@dataclass(frozen=True, slots=True)
class Window:
    """One date window of a discovery walk.

    Attributes:
        start: Lower bound.
        stop: Upper bound. Whether it is part of the window is the source's convention; see
            the module docstring.
    """

    start: datetime
    stop: datetime

    @property
    def width(self) -> timedelta:
        """Return how long the window is."""
        return self.stop - self.start

    def halved(self, floor: timedelta) -> Window:
        """Return the window halved, never narrower than a floor.

        Args:
            floor: Narrowest the window may become.

        Returns:
            A window starting where this one starts and ending halfway along it, or at the
            floor, whichever is later.
        """
        half = max(self.width / 2, floor)
        return Window(self.start, min(self.start + half, self.stop))

    def grown(self, ceiling: timedelta) -> Window:
        """Return the window doubled, never wider than a ceiling.

        The inverse of :meth:`halved`, and the reason a walk over a sparse century does not
        cost one request per day of it.

        Args:
            ceiling: Widest the window may become.

        Returns:
            A window starting where this one starts and twice as long, or as long as the
            ceiling allows, whichever is shorter.
        """
        return Window(self.start, self.start + min(self.width * 2, ceiling))

    def cursor(self, offset: int) -> str:
        """Return the opaque cursor recorded on the checkpoint for a position in the window.

        Args:
            offset: Result offset within the window.

        Returns:
            A short, readable position, well inside the 255 characters
            ``ingest_checkpoint.last_cursor`` holds.
        """
        return f"{self.start:%Y-%m-%dT%H:%M:%SZ}/{self.stop:%Y-%m-%dT%H:%M:%SZ}#{offset}"
