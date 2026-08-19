"""Cache-backed persistence for `Chronicle` — the storage-layer swap needed to
survive Azure App Service's multiple gunicorn/WSGI workers.

`Chronicle` itself (see `chronicle.py`) still holds all the narrative logic
and, by default, keeps its window in a plain `deque` guarded by a thread
lock — correct for a single process, useless the instant a second worker
exists, because each worker gets its own copy of that deque, and a worker
recycle silently wipes it. `CachedChronicle` swaps only the storage: every
read re-fetches the window from Django's cache framework and every write
goes through it too, so any worker — and any worker that gets recycled and
restarted — sees the same state. Nothing about the prompt building or the
fallback story changes; `views.py`'s `_chronicle_for()` is the one call site
that picks this class over the base one.

## Why cache, not a DB model

Two storage layers would both work; this feature's own shape and this
repo's known topology (Azure App Service, prior Redis use in `crush_lu` for
the channel layer and rate limiting) both point at cache:

- The Chronicle window is **already lossy by design** — `max_events` caps it
  at the last dozen or so orders, so it was never meant to be a durable
  record. The durable copy of each vignette lives on `Order.vignette`,
  written once at placement time and never read back through the Chronicle.
- It sits on a **guest-facing hot path** with a tight budget
  (`engine.DEFAULT_DEADLINE_SECONDS` = 1.2s just for the model call). A DB
  round trip — plus a migration, a table, and either `select_for_update()`
  or extra application bookkeeping for the concurrent-append race below —
  is more machinery than a bounded flavor-text buffer needs.
- Production already runs `django_redis.cache.RedisCache` on
  `AZURE_REDIS_CONNECTIONSTRING` for the default cache alias (see
  `azureproject/production.py`), with `IGNORE_EXCEPTIONS=True`. A Redis
  blip already degrades every other cache user in this app to a cache miss
  instead of a 500 — this module gets that resilience for free instead of
  building its own.

## The trade-off, stated plainly

A cache is not durable. A Redis restart, a maxmemory eviction, or the key's
own TTL simply expiring loses the window — the same failure mode as
today's worker restart, just rarer (App Service recycles a *worker* far
more often than Azure Cache for Redis restarts, and `CACHE_TTL_SECONDS`
below outlives any single service night). For this feature — atmospheric
flavor text printed on a drink ticket, not the order or the payment — that
is an acceptable trade. If a future requirement needed the Chronicle to be
authoritative (an audit trail, "what did table 4 see two nights ago")
that would argue for a DB-backed model instead; this module is not that.

## Concurrency

Two guests placing orders for the same venue/night at the same instant both
call `record()`. Reading the window, appending one event, and writing it
back is a read-modify-write — not atomic on its own — so two racing
get/set pairs can silently drop one guest's event. `record()` below takes a
short-lived cache-based lock (`cache.add()` used as a distributed
compare-and-swap — atomic on both `LocMemCache`, used in local dev/CI with
no `REDIS_URL` set, and `django_redis.RedisCache` in production) around the
read-modify-write. Unlike `select_for_update()`, this is not silently inert
on SQLite — it never touches a database — but it is deliberately not a true
blocking lock either: if it can't be acquired within a short, bounded
number of attempts, `record()` proceeds unlocked rather than risk stalling
a guest's ticket past its print budget. Worst case under heavy contention
is the same as an unlocked race would always be: one event lost from a
bounded flavor-text window, never a raised exception, never a blocked
request.

## Failure mode: a pickled shape a future deploy no longer understands

Django's cache framework pickles arbitrary Python objects, so a
`ChronicleEvent` written by a previous deploy could fail to unpickle after
this module's dataclass shape changes (a field added/removed, the module
moved). That failure would land inside the order-POST request, *after* the
order already committed — precisely the bare-500-after-commit failure
`chronicle.py`'s own locking comment exists to avoid. Every cache
read/write below is therefore wrapped broadly: any cache-layer exception —
connection failure, deserialization failure, anything — degrades to "start
this window over," never to a raised exception on the request.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from django.core.cache import cache as django_cache

from .chronicle import Chronicle, ChronicleEvent

#: How long a Chronicle's cache entry survives with no new orders. Generous
#: enough to span a full service night and the early hours after it without
#: relying on a scheduled sweep. `_chronicle_for()` already keys by local
#: date, so a stale entry from a previous night is simply never addressed
#: again — this TTL is a memory-hygiene backstop, not the freshness rule.
CACHE_TTL_SECONDS = 60 * 60 * 18

#: The record() lock's own TTL — short, and only a safety net: if a worker
#: died mid-critical-section (after acquiring the lock, before releasing it)
#: this is how long the venue's chronicle would otherwise stay wedged.
LOCK_TTL_SECONDS = 3
LOCK_MAX_ATTEMPTS = 8
LOCK_RETRY_SLEEP_SECONDS = 0.01


def chronicle_cache_key(venue_id, iso_date: str) -> str:
    """Cache key for one venue's one service night.

    Shared by `_chronicle_for()` and the tests so the key format lives in
    exactly one place. `venue_id` is a UUID (see `models.Venue.id`); the
    f-string below works whether it's passed as a `UUID` or already a `str`.
    """
    return f"atmos:chronicle:{venue_id}:{iso_date}"


@dataclass
class CachedChronicle(Chronicle):
    """A `Chronicle` whose window lives in Django's cache, not process memory.

    Same public API as `Chronicle` — every call site (`views.py`'s
    `_chronicle_for()`, `lore.engine.generate_vignette()`,
    `lore.chronicle.build_prompt()`) is unchanged; only `_snapshot()` and
    `record()` are overridden to go through the cache instead of the deque.
    """

    cache_key: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.cache_key:
            raise ValueError("CachedChronicle requires a non-empty cache_key")

    def _snapshot(self) -> tuple[ChronicleEvent, ...]:
        try:
            return django_cache.get(self.cache_key) or ()
        except Exception:  # noqa: BLE001 - see module docstring: a cache-layer
            return ()  # failure must degrade to "empty room", never raise.

    def record(self, event: ChronicleEvent) -> None:
        lock_key = f"{self.cache_key}:lock"
        acquired = False
        try:
            for _ in range(LOCK_MAX_ATTEMPTS):
                if django_cache.add(lock_key, "1", timeout=LOCK_TTL_SECONDS):
                    acquired = True
                    break
                time.sleep(LOCK_RETRY_SLEEP_SECONDS)
        except Exception:  # noqa: BLE001 - proceed unlocked; see module docstring.
            acquired = False

        try:
            events = list(self._snapshot())
            events.append(event)
            cap = max(1, self.max_events)
            if len(events) > cap:
                events = events[-cap:]
            django_cache.set(
                self.cache_key, tuple(events), timeout=CACHE_TTL_SECONDS
            )
        except Exception:  # noqa: BLE001 - a ticket must print regardless of
            pass  # whether its own event made it into the shared window.
        finally:
            if acquired:
                try:
                    django_cache.delete(lock_key)
                except Exception:  # noqa: BLE001 - LOCK_TTL_SECONDS reclaims it.
                    pass
