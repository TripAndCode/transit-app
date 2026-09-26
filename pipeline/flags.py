"""Feature-flag registry: DB-backed overrides with an env-var fallback.

`flag(key, env_default)` is the single read path every gated feature calls,
replacing a direct `os.environ.get(ENV_VAR, default)`. Every call goes
through an in-process cache shared by every key, refreshed at most once per
`_CACHE_TTL_SECONDS`, so a hot path costs at most one Postgres round trip per
cache window process-wide -- not one per key, and not one per call. A
missing `feature_flags` table (not migrated yet) or an unreachable database
falls back to each key's env var/default and never raises; the flag behaves
exactly as it did before this module existed.

Reads happen at the call site, not at import time: every caller re-checks on
each request/job run, so a PATCH override or an env change takes effect
without a process restart. Callers that used to read their env var once at
import time must move the read into their request/job path for this to hold
-- see `api.main`'s OpenAPI docs gate for an example.

The read path comes in two shapes, and which one a caller must use follows
from where it runs:

- `aflag`/`aget_flag_state` are for anything running on the event loop --
  every `async def` handler. They are the only callers that may perform the
  blocking refresh, and they do it via `asyncio.to_thread`, never inline.
- `flag`/`get_flag_state` are for pipeline and CLI callers, and for the
  synchronous FastAPI dependencies the framework already runs in its
  threadpool. They block on the database only when nothing is cached at
  all; otherwise they serve the cache and let someone else refresh it.

The trade-off that split buys: after `invalidate()` the owed refresh belongs
to the next async caller, so a synchronous caller may keep seeing the old
value for up to `_CACHE_TTL_SECONDS`. In a process with no async readers at
all (a CLI run) it sees the old value until the next `warm()`. Correctness
for the admin surface is unaffected -- the PATCH/DELETE handlers are async
and resolve their own response through the async path, so the write is
visible in the response that reports it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import psycopg2

from pipeline.cache import _REGISTERED_CLEARS

_log = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30.0
_TRUE_STRINGS = ("1", "true", "yes")
_FALSE_STRINGS = ("0", "false", "no")
_CONNECT_TIMEOUT_SECONDS = 2
#: `connect_timeout` bounds only the handshake, so a query that is accepted
#: and then stalls would keep a refresh thread alive indefinitely -- and the
#: "one refresh at a time" guard would see it still running and never start
#: another, leaving the cache frozen for the life of the process.
_STATEMENT_TIMEOUT_MS = 5000


@dataclass(frozen=True)
class FlagDefinition:
    """One entry in the known-flags registry.

    `env_default` is the boolean `os.environ.get(env_var, ...)` used at this
    flag's call site before it was registered here -- the fallback whenever
    there is no DB override and no env var set.
    """

    key: str
    env_var: str
    label_key: str
    env_default: bool


REGISTRY: tuple[FlagDefinition, ...] = (
    FlagDefinition("ask_router_enabled", "ASK_ROUTER_ENABLED", "admin.flags.labels.askRouterEnabled", True),
    FlagDefinition("ask_followup_enabled", "ASK_FOLLOWUP_ENABLED", "admin.flags.labels.askFollowupEnabled", False),
    FlagDefinition(
        "copilot_insight_enabled", "COPILOT_INSIGHT_ENABLED", "admin.flags.labels.copilotInsightEnabled", False
    ),
    FlagDefinition("ask_history_enabled", "ASK_HISTORY_ENABLED", "admin.flags.labels.askHistoryEnabled", True),
    FlagDefinition(
        "ask_intent_cache_enabled", "ASK_INTENT_CACHE_ENABLED", "admin.flags.labels.askIntentCacheEnabled", False
    ),
    FlagDefinition("ask_query_log_enabled", "ASK_QUERY_LOG_ENABLED", "admin.flags.labels.askQueryLogEnabled", True),
    FlagDefinition(
        "weather_ingest_enabled", "WEATHER_INGEST_ENABLED", "admin.flags.labels.weatherIngestEnabled", False
    ),
    FlagDefinition("openapi_docs_enabled", "OPENAPI_DOCS_ENABLED", "admin.flags.labels.openapiDocsEnabled", False),
    FlagDefinition("perf_debug_enabled", "PERF_DEBUG_ENABLED", "admin.flags.labels.perfDebugEnabled", False),
)

_BY_KEY = {d.key: d for d in REGISTRY}


@dataclass(frozen=True)
class FlagState:
    """The resolved value of one flag plus its provenance, for the admin API."""

    key: str
    value: bool
    source: str  # "env" | "override"
    env_default: bool
    updated_by: int | None
    updated_at: Any | None
    reason: str | None


_cache: dict[str, FlagState] = {}
_cache_expires_at = 0.0
_cache_lock = threading.Lock()
_refresh_thread: threading.Thread | None = None
_refresh_owed = False
#: Bumped by `invalidate()`; a refresh that began under an older value read
#: the database before the write it is meant to pick up.
_generation = 0
#: Ticket dispenser for refreshes, and the highest ticket whose result has
#: been committed. A counter rather than a timestamp: ordering must not
#: depend on a clock, which a caller (or a test) can move.
_refresh_seq = 0
_committed_seq = 0


def _env_bool(env_var: str, default: bool) -> bool:
    """Resolve one env var, falling back to the flag's own default.

    A value this does not recognise resolves to the default rather than to
    False. The default-on switches here were previously read as "off only
    when the value is exactly `false`", so treating an unrecognised value
    as off would quietly disable a feature in any deployment that had set
    one of them to something like `on` or `enabled`.
    """
    raw = os.environ.get(env_var)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE_STRINGS:
        return True
    if value in _FALSE_STRINGS:
        return False
    return default


def _load_overrides() -> dict[str, tuple[bool, str | None, int | None, Any]] | None:
    """Read every row of `feature_flags` in one round trip.

    Returns an empty dict -- "no overrides, use env for everything" -- when
    `DATABASE_URL` is unset or the table does not exist yet, both of which
    mean there is genuinely nothing to override with.

    Returns `None` for a transient failure (unreachable database, query
    error). That is deliberately not the same answer: an override exists to
    switch something off, and an incident is exactly when the database is
    also likely to be unwell. Treating "cannot read" as "no overrides" would
    quietly restore the env default and turn the feature back on at the
    worst possible moment, so the caller keeps whatever it last read instead.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return {}
    try:
        conn = psycopg2.connect(
            db_url,
            connect_timeout=_CONNECT_TIMEOUT_SECONDS,
            options=f"-c statement_timeout={_STATEMENT_TIMEOUT_MS}",
        )
    except psycopg2.Error:
        _log.warning("flags: could not connect to read feature_flags; keeping last known values", exc_info=True)
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT key, value, reason, updated_by, updated_at FROM feature_flags")
            rows = cur.fetchall()
    except psycopg2.errors.UndefinedTable:
        _log.warning("flags: feature_flags table absent; using env defaults", exc_info=True)
        return {}
    except psycopg2.Error:
        _log.warning("flags: could not query feature_flags; keeping last known values", exc_info=True)
        return None
    finally:
        conn.close()
    return {key: (bool(value), reason, updated_by, updated_at) for key, value, reason, updated_by, updated_at in rows}


def _build_states(overrides: dict[str, tuple[bool, str | None, int | None, Any]]) -> dict[str, FlagState]:
    """Resolve every registered flag against `overrides` and the environment."""
    states: dict[str, FlagState] = {}
    for definition in REGISTRY:
        env_default = _env_bool(definition.env_var, definition.env_default)
        override = overrides.get(definition.key)
        if override is None:
            states[definition.key] = FlagState(
                key=definition.key,
                value=env_default,
                source="env",
                env_default=env_default,
                updated_by=None,
                updated_at=None,
                reason=None,
            )
        else:
            value, reason, updated_by, updated_at = override
            states[definition.key] = FlagState(
                key=definition.key,
                value=value,
                source="override",
                env_default=env_default,
                updated_by=updated_by,
                updated_at=updated_at,
                reason=reason,
            )
    return states


def _refresh() -> None:
    """Read the overrides, then swap the resolved states into the cache.

    The read happens with no lock held. Holding `_cache_lock` across the
    database round trip would make every concurrent reader wait on it,
    which is the same stall this exists to avoid -- just moved off one
    unlucky caller and onto all of them. The lock covers only the
    in-memory swap.
    """
    global _refresh_seq
    with _cache_lock:
        started_generation = _generation
        _refresh_seq += 1
        ticket = _refresh_seq

    overrides = _load_overrides()

    global _cache, _cache_expires_at, _refresh_owed, _committed_seq
    with _cache_lock:
        # Two refreshes can be in flight at once -- a background one from an
        # expiry, and a synchronous one forced by an admin write. Last to
        # finish would otherwise win, so a slow read that began before the
        # write could overwrite the value it just stored. Discard a result
        # the cache has already moved past: a newer read has committed, or
        # an invalidate has happened since, meaning this data predates the
        # write that prompted it.
        if started_generation != _generation or ticket < _committed_seq:
            return
        _committed_seq = ticket
        # This read is current, so it satisfies an outstanding "the next
        # read must see this" promise -- otherwise a startup warm leaves the
        # first request to redo the same blocking read. A superseded read
        # returns above without clearing it, so the promise survives.
        _refresh_owed = False
        if overrides is None:
            # Transient read failure: carry forward only the entries an
            # operator actually overrode. An override exists to switch
            # something off, and an incident is exactly when the database is
            # also likely to be unwell, so letting "cannot read" mean "no
            # overrides" would switch it back on at the worst moment.
            # Everything else re-resolves from the environment, so a local
            # toggle is not inert while the database is down.
            overrides = {
                key: (state.value, state.reason, state.updated_by, state.updated_at)
                for key, state in _cache.items()
                if state.source == "override"
            }
        _cache = _build_states(overrides)
        _cache_expires_at = time.monotonic() + _CACHE_TTL_SECONDS


def _start_background_refresh() -> None:
    """Refresh behind the readers, who keep seeing the cached values.

    A refresh must never run on a reader's own thread, so an expiry is
    absorbed here rather than charged to whoever happened to arrive first.
    The expiry is pushed out before the thread starts so arrivals during a
    slow refresh read the cache rather than queueing more refreshes.
    """
    global _refresh_thread, _cache_expires_at
    with _cache_lock:
        if _refresh_thread is not None and _refresh_thread.is_alive():
            return
        _cache_expires_at = time.monotonic() + _CACHE_TTL_SECONDS
        _refresh_thread = threading.Thread(target=_refresh, name="flags-refresh", daemon=True)
        _refresh_thread.start()


def warm() -> None:
    """Resolve every flag now, so no request has to.

    Blocking, and meant for startup, off the event loop. Without it the
    first flag touched pays for the read: an async caller hands it to a
    worker thread, but a synchronous one does it inline, and a synchronous
    one with nothing cached has no cached value to fall back on.
    """
    _refresh()


#: What a reader must do about the cache entry it just looked at.
_FRESH = "fresh"  # inside the TTL window; use it
_EXPIRED = "expired"  # serve it, refresh behind the reader
_OWED = "owed"  # nothing cached, or an `invalidate()` promised a re-read


def _peek(key: str) -> tuple[FlagState | None, str]:
    """Look up `key` and classify what the caller owes the cache.

    Split out of the read paths because the classification is identical for
    a synchronous and an asynchronous reader -- only what they are allowed
    to do about `_OWED` differs.
    """
    if key not in _BY_KEY:
        raise KeyError(f"unknown feature flag: {key!r}")
    with _cache_lock:
        cached = _cache.get(key)
        if cached is None:
            return None, _OWED
        # The marker is cleared by whichever refresh actually commits, not
        # here: clearing it up front lets a second reader arriving moments
        # later see it already satisfied and serve the value the write was
        # supposed to replace.
        if _refresh_owed:
            return cached, _OWED
        if time.monotonic() < _cache_expires_at:
            return cached, _FRESH
        return cached, _EXPIRED


def _resolve_after_refresh(key: str, cached: FlagState | None) -> FlagState:
    """The state to answer with once a refresh this caller waited on is done."""
    with _cache_lock:
        resolved = _cache.get(key)
    if resolved is not None:
        return resolved
    if cached is not None:
        return cached
    # The refresh was superseded before it could commit and nothing has ever
    # been cached, so there is no override this process knows of. Resolve
    # from the environment rather than raising: `flag()` is a kill switch
    # read from request paths, and it is documented never to raise.
    return _build_states({})[key]


def get_flag_state(key: str) -> FlagState:
    """Return the full resolved state (value + provenance) for `key`.

    The synchronous read path, for pipeline/CLI callers and for synchronous
    FastAPI dependencies (which the framework runs in its threadpool). It
    blocks on Postgres only when this process has never resolved `key` at
    all; with anything cached it answers from the cache, so an `async def`
    handler that reaches here by mistake stalls the event loop for no longer
    than a dict lookup. A refresh owed by `invalidate()` is left for an
    async caller -- see the module docstring for the staleness this admits.

    Raises `KeyError` for a key not in `REGISTRY` -- every caller of `flag()`
    is expected to pass a registered key, and a typo here should fail loud
    rather than silently always resolving to its literal `env_default`.
    """
    cached, state = _peek(key)
    if state == _FRESH:
        assert cached is not None
        return cached
    if cached is not None:
        if state == _EXPIRED:
            _start_background_refresh()
        return cached
    _refresh()
    return _resolve_after_refresh(key, cached)


async def aget_flag_state(key: str) -> FlagState:
    """`get_flag_state` for callers on the event loop.

    The one read path allowed to perform the blocking refresh, because it is
    the one that can hand it to a worker thread. That makes it also the path
    that keeps `invalidate()`'s promise: after an admin write, the first
    async reader does the re-read and everyone else sees the new value.
    """
    cached, state = _peek(key)
    if state == _FRESH:
        assert cached is not None
        return cached
    if state == _EXPIRED:
        assert cached is not None
        _start_background_refresh()
        return cached
    await asyncio.to_thread(_refresh)
    return _resolve_after_refresh(key, cached)


def flag(key: str, env_default: bool) -> bool:
    """Return the current value of the flag `key`.

    `env_default` is the caller's own fallback literal, kept for parity with
    the direct `os.environ.get(ENV_VAR, default)` this replaces; the
    registry's `FlagDefinition.env_default` is the source of truth used to
    build the admin API's response, and the two must agree (see
    `test_registry_env_defaults_match_prior_hardcoded_defaults`).
    """
    return get_flag_state(key).value


async def aflag(key: str, /) -> bool:
    """`flag()` for callers on the event loop.

    Takes no `env_default`: the registry is the only source of that fallback,
    and a second copy at the call site is one more thing that can drift.
    """
    return (await aget_flag_state(key)).value


def invalidate() -> None:
    """Mark the cache stale so the next *async* read re-reads the DB
    immediately, instead of waiting out the TTL.

    Called after a write to `/api/admin/flags/:key` so the API's own next
    GET -- and the very next gated request anywhere in the process -- sees
    the new value without delay. The owed read is deliberately an async
    caller's to perform: it is a blocking psycopg2 round trip, and only the
    async path can put it on a worker thread rather than the event loop.
    """
    global _cache_expires_at, _refresh_owed, _generation
    with _cache_lock:
        # Anything already reading is now reading pre-write data.
        _generation += 1
        # Marked stale rather than emptied. The next read replaces every
        # entry on success, so nothing leaks between tests that swap env
        # vars; but if that read fails -- including the one right after a
        # PATCH -- the override just written is still there to carry
        # forward instead of being dropped on the floor.
        _cache_expires_at = 0.0
        # An explicit invalidation is a promise that the next read sees the
        # new value, so it re-reads on the calling thread rather than
        # serving a stale entry while a background refresh catches up. The
        # callers are the admin PATCH and the test suite, not a hot path.
        _refresh_owed = True


def reset_cache() -> None:
    """Forget every entry, leaving the cache as it was at process start.

    Distinct from `invalidate()`, which keeps the entries precisely so a
    live override survives a failed re-read -- and, since a synchronous
    reader is served those entries, keeps answering with them. That is the
    right production behaviour and the wrong one for a caller asking for a
    clean slate, which is what a between-test fixture and the debug
    cache-reset endpoint both mean.
    """
    global _cache, _cache_expires_at, _generation, _refresh_owed
    with _cache_lock:
        _generation += 1
        _cache = {}
        _cache_expires_at = 0.0
        _refresh_owed = False


# Reuse pipeline.cache's existing "clear every cache" registry (wired into
# the per-test autouse fixture and the perf-debug reset endpoint) so this
# module's cache is swept alongside every other one, without every caller
# needing to know this module exists.
_REGISTERED_CLEARS.append(reset_cache)
