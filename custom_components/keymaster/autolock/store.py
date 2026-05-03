"""Persistent autolock timer store.

One TimerStore instance per coordinator. Owns an asyncio.Lock so
concurrent operations from different timers (which all write to the
same disk store) can't interleave their load+modify+save sequences.

The on-disk shape is `{timer_id: {"end_time": iso, "duration": int}}`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime as dt
import logging
from typing import TypedDict

from custom_components.keymaster.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

TIMER_STORAGE_VERSION = 1
TIMER_STORAGE_KEY = f"{DOMAIN}.timers"

_LOGGER: logging.Logger = logging.getLogger(__name__)


class _TimerStoreEntryDict(TypedDict):
    """Persisted shape for a single timer entry."""

    end_time: str
    duration: int


@dataclass(frozen=True)
class TimerEntry:
    """A typed, validated timer entry.

    Validates on construction so direct callers (tests, AutolockTimer.start)
    get the same guarantees as parsed-from-disk entries: end_time must be
    timezone-aware, duration must be non-negative.
    """

    end_time: dt
    duration: int

    def __post_init__(self) -> None:
        """Reject naive datetimes and negative durations at construction."""
        if self.end_time.tzinfo is None:
            raise ValueError("TimerEntry.end_time must be timezone-aware")
        if self.duration < 0:
            raise ValueError(f"TimerEntry.duration must be non-negative, got {self.duration}")


class TimerStore:
    """Atomic persistence layer for autolock timers.

    All public methods are async and acquire a shared lock so concurrent
    operations from different AutolockTimer instances writing to the
    same disk store can't lose updates.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize with a fresh asyncio.Lock and the HA Store handle."""
        self._store: Store[dict[str, _TimerStoreEntryDict]] = Store(
            hass, TIMER_STORAGE_VERSION, TIMER_STORAGE_KEY
        )
        self._lock = asyncio.Lock()

    async def read(self, timer_id: str) -> TimerEntry | None:
        """Return the entry for `timer_id`, or None if absent or invalid.

        Invalid (corrupt) entries are removed as a side effect so a
        subsequent read returns None cleanly.
        """
        async with self._lock:
            data = await self._store.async_load() or {}
            raw = data.get(timer_id)
            if raw is None:
                return None
            entry = self._parse(timer_id, raw)
            if entry is None:
                # Corrupt — remove inline so callers don't see it again
                del data[timer_id]
                await self._store.async_save(data)
            return entry

    async def write(self, timer_id: str, entry: TimerEntry) -> None:
        """Persist `entry` under `timer_id`, replacing any prior value."""
        async with self._lock:
            data = await self._store.async_load() or {}
            data[timer_id] = {
                "end_time": entry.end_time.isoformat(),
                "duration": entry.duration,
            }
            await self._store.async_save(data)

    async def remove(self, timer_id: str) -> None:
        """Remove the entry for `timer_id` if present. Idempotent."""
        async with self._lock:
            data = await self._store.async_load() or {}
            if timer_id in data:
                del data[timer_id]
                await self._store.async_save(data)

    @staticmethod
    def _parse(timer_id: str, raw: _TimerStoreEntryDict) -> TimerEntry | None:
        """Parse a raw store entry into a TimerEntry, or None if invalid."""
        try:
            end_time = dt.fromisoformat(raw["end_time"])
        except (KeyError, TypeError, ValueError):
            _LOGGER.warning(
                "[TimerStore] %s: invalid persisted end_time, treating as absent",
                timer_id,
            )
            return None
        if end_time.tzinfo is None:
            # Legacy/manually-edited entries may be naive; treat as UTC
            end_time = dt_util.as_utc(end_time)
        try:
            duration = int(raw.get("duration", 0))
        except (TypeError, ValueError):
            _LOGGER.warning(
                "[TimerStore] %s: invalid persisted duration, treating as absent",
                timer_id,
            )
            return None
        return TimerEntry(end_time=end_time, duration=duration)
