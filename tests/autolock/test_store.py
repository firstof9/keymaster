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
    await store.write("t1", entry)
    loaded = await store.read("t1")
    assert loaded is not None
    assert loaded.end_time == entry.end_time
    assert loaded.duration == entry.duration


async def test_remove_deletes_entry(store, entry):
    await store.write("t1", entry)
    await store.remove("t1")
    assert await store.read("t1") is None


async def test_remove_absent_is_noop(store):
    await store.remove("never-existed")  # must not raise


async def test_write_overwrites_existing(store, entry):
    await store.write("t1", entry)
    later = TimerEntry(end_time=entry.end_time + timedelta(minutes=10), duration=900)
    await store.write("t1", later)
    loaded = await store.read("t1")
    assert loaded == later


async def test_multiple_timer_ids_isolated(store, entry):
    other = TimerEntry(end_time=entry.end_time + timedelta(minutes=10), duration=42)
    await store.write("a", entry)
    await store.write("b", other)
    assert await store.read("a") == entry
    assert await store.read("b") == other
    await store.remove("a")
    assert await store.read("a") is None
    assert await store.read("b") == other


async def test_corrupt_entry_is_removed_on_read(hass, store, entry):
    """Corrupt entries are pruned so callers see them as absent."""
    # Plant a malformed entry by going around the public API
    await store._store.async_save({"corrupt": {"end_time": "not-a-date", "duration": 5}})
    assert await store.read("corrupt") is None
    # Confirm it was removed
    raw = await store._store.async_load() or {}
    assert "corrupt" not in raw


async def test_naive_end_time_treated_as_utc(store):
    """Legacy/manually-edited naive datetimes are coerced rather than rejected."""
    naive_iso = (dt.utcnow() + timedelta(minutes=5)).isoformat()
    await store._store.async_save({"legacy": {"end_time": naive_iso, "duration": 300}})
    loaded = await store.read("legacy")
    assert loaded is not None
    assert loaded.end_time.tzinfo is not None


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
