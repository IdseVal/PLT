"""The window arithmetic both connectors resize their discovery walks with.

Small, but shared and load-bearing: a `halved` that ever returned the window unchanged would
turn a narrowing loop into an infinite one, and a `grown` that ever exceeded its ceiling would
undo the bound the ceiling exists to place on a single request.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from plt.pipeline.windows import Window

START = datetime(2026, 6, 1, tzinfo=UTC)


def test_a_window_reports_its_own_width() -> None:
    assert Window(START, START + timedelta(days=3)).width == timedelta(days=3)


def test_halving_takes_the_first_half() -> None:
    halved = Window(START, START + timedelta(days=4)).halved(timedelta(seconds=1))

    assert halved.start == START
    assert halved.stop == START + timedelta(days=2)


def test_halving_stops_at_the_floor_rather_than_below_it() -> None:
    halved = Window(START, START + timedelta(seconds=3)).halved(timedelta(seconds=2))

    assert halved.width == timedelta(seconds=2)


def test_a_window_already_at_the_floor_is_returned_whole_rather_than_widened() -> None:
    """The termination condition of every narrowing loop that uses this.

    Halving a window narrower than the floor must not hand back a *wider* one: a loop that
    checked ``width > floor`` would then never see the width stop changing.
    """
    window = Window(START, START + timedelta(seconds=1))

    assert window.halved(timedelta(seconds=30)) == window


def test_growing_doubles_the_window() -> None:
    grown = Window(START, START + timedelta(days=2)).grown(timedelta(days=365))

    assert grown.start == START
    assert grown.width == timedelta(days=4)


def test_growing_stops_at_the_ceiling() -> None:
    grown = Window(START, START + timedelta(days=300)).grown(timedelta(days=365))

    assert grown.width == timedelta(days=365)


def test_the_cursor_names_the_window_and_the_position_in_it() -> None:
    cursor = Window(START, START + timedelta(days=1)).cursor(42)

    assert cursor == "2026-06-01T00:00:00Z/2026-06-02T00:00:00Z#42"
    # ``ingest_checkpoint.last_cursor`` holds 255 characters; a position has to fit in one.
    assert len(cursor) < 255


def test_a_window_is_hashable_and_compares_by_value() -> None:
    """Frozen on purpose: a walk passes windows around and must not mutate one in flight."""
    first = Window(START, START + timedelta(days=1))
    second = Window(START, START + timedelta(days=1))

    assert first == second
    assert len({first, second}) == 1
