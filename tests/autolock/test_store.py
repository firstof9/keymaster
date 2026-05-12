"""Tests for the TimerStore persistence layer."""

from __future__ import annotations

import asyncio
from datetime import datetime as dt, timedelta
from unittest.mock import AsyncMock

import pytest

from custom_components.keymaster.autolock.store import TimerEntry, TimerStore
from homeassistant.util import dt as dt_util


@pytest.fixture
def store(hass):
    """Provide a TimerStore wired to a real HA Store."""
    return TimerStore(hass)


@pytest.fixture
def entry() -> TimerEntry:
    """Provide a fresh-in-the-future entry."""
    return TimerEntry(end_time=dt_util.utcnow() + timedelta(minutes=5), duration=300)


async def test_read_absent_returns_none(store):
    """Reading a missing timer_id yields None without raising."""
    assert await store.read("missing") is None


async def test_write_then_read_roundtrips(store, entry):
    """write() persists, read() returns the same entry."""
    await store.write("t1", entry)
    loaded = await store.read("t1")
    assert loaded is not None
    assert loaded.end_time == entry.end_time
    assert loaded.duration == entry.duration


async def test_remove_deletes_entry(store, entry):
    """remove() purges the entry; subsequent read() returns None."""
    await store.write("t1", entry)
    await store.remove("t1")
    assert await store.read("t1") is None


async def test_remove_absent_is_noop(store):
    """remove() on a missing timer_id is a silent noop."""
    await store.remove("never-existed")  # must not raise


async def test_write_overwrites_existing(store, entry):
    """A second write() under the same timer_id replaces the prior value."""
    await store.write("t1", entry)
    later = TimerEntry(end_time=entry.end_time + timedelta(minutes=10), duration=900)
    await store.write("t1", later)
    loaded = await store.read("t1")
    assert loaded == later


async def test_multiple_timer_ids_isolated(store, entry):
    """Operations on one timer_id don't affect another."""
    other = TimerEntry(end_time=entry.end_time + timedelta(minutes=10), duration=42)
    await store.write("a", entry)
    await store.write("b", other)
    assert await store.read("a") == entry
    assert await store.read("b") == other
    await store.remove("a")
    assert await store.read("a") is None
    assert await store.read("b") == other


async def test_non_mapping_entry_is_removed_on_read(store):
    """Persisted entry that's not a dict (e.g. legacy list/string) is pruned.

    Regression: `_parse` previously called `.get()` on `raw` unconditionally,
    so a non-mapping value would AttributeError and break recovery.
    """
    await store._store.async_save({"weird": ["unexpected", "list"]})
    assert await store.read("weird") is None
    raw = await store._store.async_load() or {}
    assert "weird" not in raw


async def test_negative_duration_entry_is_removed_on_read(store):
    """A persisted entry with a negative duration is pruned.

    Regression: TimerEntry's __post_init__ raises ValueError for negative
    duration, but `_parse` didn't catch it — would crash recovery.
    """
    future = (dt_util.utcnow() + timedelta(minutes=5)).isoformat()
    await store._store.async_save({"bad": {"end_time": future, "duration": -1}})
    assert await store.read("bad") is None
    raw = await store._store.async_load() or {}
    assert "bad" not in raw


async def test_corrupt_entry_is_removed_on_read(hass, store, entry):
    """Corrupt entries are pruned so callers see them as absent."""
    # Plant a malformed entry by going around the public API
    await store._store.async_save({"corrupt": {"end_time": "not-a-date", "duration": 5}})
    assert await store.read("corrupt") is None
    # Confirm it was removed
    raw = await store._store.async_load() or {}
    assert "corrupt" not in raw


async def test_naive_end_time_interpreted_as_utc(store):
    """Naive on-disk datetimes are interpreted as UTC, not local time.

    Regression: an earlier version used `dt_util.as_utc()` which converts
    from local/default-zone, so the loaded value would be wrong on any
    non-UTC HA install. The fix uses `replace(tzinfo=UTC)` to interpret
    the stored value as already-UTC. Compare loaded value against the
    same instant treated as UTC — equality (not just tz-awareness) is
    what catches the regression.
    """
    # Specific UTC instant: 2030-01-01 12:34:56Z
    known_utc = dt(2030, 1, 1, 12, 34, 56, tzinfo=dt_util.UTC)
    naive_iso = known_utc.replace(tzinfo=None).isoformat()
    await store._store.async_save({"legacy": {"end_time": naive_iso, "duration": 300}})

    loaded = await store.read("legacy")
    assert loaded is not None
    assert loaded.end_time.tzinfo is not None
    # The load must produce the SAME UTC instant — `as_utc` would have
    # shifted it by the local UTC offset on non-UTC test hosts.
    assert loaded.end_time == known_utc


async def test_concurrent_writes_serialized_by_lock(hass, store):
    """Two concurrent writes must serialize — neither overwrites the other.

    Without the shared lock, both would load the empty dict, each set
    their own key, and the later async_save would clobber the earlier.
    """
    e1 = TimerEntry(end_time=dt_util.utcnow() + timedelta(minutes=5), duration=300)
    e2 = TimerEntry(end_time=dt_util.utcnow() + timedelta(minutes=10), duration=600)

    # Make load yield so the writes have a chance to interleave
    real_load = store._store.async_load
    real_save = store._store.async_save
    save_calls = []

    async def slow_load():
        await asyncio.sleep(0)
        return await real_load()

    async def tracking_save(data):
        save_calls.append(dict(data))
        await real_save(data)

    store._store.async_load = AsyncMock(side_effect=slow_load)
    store._store.async_save = AsyncMock(side_effect=tracking_save)

    await asyncio.gather(store.write("a", e1), store.write("b", e2))

    final = await store._store.async_load() or {}
    assert "a" in final and "b" in final, f"expected both keys, got {final}"
