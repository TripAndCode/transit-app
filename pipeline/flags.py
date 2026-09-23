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
"""

from __future__ import annotations

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
_force_sync_refresh = False


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
        conn = psycopg2.connect(db_url, connect_timeout=_CONNECT_TIMEOUT_SECONDS)
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


def _refresh_cache_locked() -> None:
    """Rebuild `_cache` for every registered flag. Caller holds `_cache_lock`."""
    global _cache, _cache_expires_at
    overrides = _load_overrides()
    if overrides is None:
        # Transient read failure: carry forward only the entries an operator
        # actually overrode. Those are the ones worth protecting -- an
        # override exists to switch something off, and an incident is
        # exactly when the database is also likely to be unwell, so letting
        # "cannot read" mean "no overrides" would switch it back on at the
        # worst moment. Everything else re-resolves from the environment,
        # which stays responsive and keeps the never-raises contract when
        # nothing has been read yet.
        overrides = {
            key: (state.value, state.reason, state.updated_by, state.updated_at)
            for key, state in _cache.items()
            if state.source == "override"
        }
    new_cache: dict[str, FlagState] = {}
    for definition in REGISTRY:
        env_default = _env_bool(definition.env_var, definition.env_default)
        override = overrides.get(definition.key)
        if override is None:
            new_cache[definition.key] = FlagState(
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
            new_cache[definition.key] = FlagState(
                key=definition.key,
                value=value,
                source="override",
                env_default=env_default,
                updated_by=updated_by,
                updated_at=updated_at,
                reason=reason,
            )
    _cache = new_cache
    _cache_expires_at = time.monotonic() + _CACHE_TTL_SECONDS


def _start_background_refresh_locked() -> None:
    """Refresh off the caller's thread, serving the cached values meanwhile.

    `flag()` is called from `async def` request handlers, so the refresh
    cannot run inline: `psycopg2.connect` plus the SELECT are blocking, and
    on the event-loop thread they stall every concurrent request the worker
    is serving, not just the caller. Serving a value up to one window stale
    is the right trade against that -- and the expiry is pushed out before
    the thread starts, so callers arriving during a slow refresh keep
    reading the cache instead of queueing another one.
    """
    global _refresh_thread, _cache_expires_at
    if _refresh_thread is not None and _refresh_thread.is_alive():
        return
    _cache_expires_at = time.monotonic() + _CACHE_TTL_SECONDS
    _refresh_thread = threading.Thread(target=_refresh_in_background, name="flags-refresh", daemon=True)
    _refresh_thread.start()


def _refresh_in_background() -> None:
    with _cache_lock:
        _refresh_cache_locked()


def warm() -> None:
    """Resolve every flag now, so the first request does not have to.

    Blocking, and meant to be called from startup (off the event loop) --
    `get_flag_state` otherwise does this read inline the first time a flag
    is touched, which on an async handler is the one place it must not.
    """
    with _cache_lock:
        _refresh_cache_locked()


def get_flag_state(key: str) -> FlagState:
    """Return the full resolved state (value + provenance) for `key`.

    Raises `KeyError` for a key not in `REGISTRY` -- every caller of `flag()`
    is expected to pass a registered key, and a typo here should fail loud
    rather than silently always resolving to its literal `env_default`.

    The first resolution in a process reads the database on the calling
    thread, because there is nothing cached to serve and an override must
    not be missed; every refresh after that happens in the background.
    """
    if key not in _BY_KEY:
        raise KeyError(f"unknown feature flag: {key!r}")
    global _force_sync_refresh
    with _cache_lock:
        if key not in _cache or _force_sync_refresh:
            # Nothing to serve, or an override was just written and the
            # caller was promised it would be visible immediately.
            _force_sync_refresh = False
            _refresh_cache_locked()
        elif time.monotonic() >= _cache_expires_at:
            _start_background_refresh_locked()
        return _cache[key]


def flag(key: str, env_default: bool) -> bool:
    """Return the current value of the flag `key`.

    `env_default` is the caller's own fallback literal, kept for parity with
    the direct `os.environ.get(ENV_VAR, default)` this replaces; the
    registry's `FlagDefinition.env_default` is the source of truth used to
    build the admin API's response, and the two must agree (see
    `test_registry_env_defaults_match_prior_hardcoded_defaults`).
    """
    return get_flag_state(key).value


def invalidate() -> None:
    """Drop the cache so the next `flag()`/`get_flag_state()` call re-reads
    the DB immediately, instead of waiting out the TTL.

    Called after a PATCH to `/api/admin/flags/:key` so the API's own next GET
    -- and the very next gated request anywhere in the process -- sees the
    new value without delay. Also used by the test suite to keep flag state
    from leaking between tests that monkeypatch env vars.
    """
    global _cache_expires_at, _force_sync_refresh
    with _cache_lock:
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
        _force_sync_refresh = True


# Reuse pipeline.cache's existing "clear every cache" registry (wired into
# the per-test autouse fixture and the perf-debug reset endpoint) so this
# module's cache is swept alongside every other one, without every caller
# needing to know this module exists.
_REGISTERED_CLEARS.append(invalidate)
