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
    # Registered even though A15 (the PR that introduced this env var) may
    # not have merged yet in every checkout -- see this task's own report
    # for the merge-order note. Harmless if the call site still reads the
    # env var directly: this registry entry just makes the admin UI able to
    # show/override it once the call site is switched over too.
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


def _env_bool(env_var: str, default: bool) -> bool:
    raw = os.environ.get(env_var)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_STRINGS


def _load_overrides() -> dict[str, tuple[bool, str | None, int | None, Any]]:
    """Read every row of `feature_flags` in one round trip.

    Returns an empty dict -- meaning "no overrides, use env for everything"
    -- when `DATABASE_URL` is unset, the database is unreachable, or the
    table doesn't exist yet (fresh deployment pending this task's migration).
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return {}
    try:
        conn = psycopg2.connect(db_url, connect_timeout=_CONNECT_TIMEOUT_SECONDS)
    except psycopg2.Error:
        _log.warning("flags: could not connect to read feature_flags; using env defaults", exc_info=True)
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT key, value, reason, updated_by, updated_at FROM feature_flags")
            rows = cur.fetchall()
    except psycopg2.Error:
        _log.warning("flags: could not query feature_flags; using env defaults", exc_info=True)
        return {}
    finally:
        conn.close()
    return {key: (bool(value), reason, updated_by, updated_at) for key, value, reason, updated_by, updated_at in rows}


def _refresh_cache_locked() -> None:
    """Rebuild `_cache` for every registered flag. Caller holds `_cache_lock`."""
    global _cache, _cache_expires_at
    overrides = _load_overrides()
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


def get_flag_state(key: str) -> FlagState:
    """Return the full resolved state (value + provenance) for `key`.

    Raises `KeyError` for a key not in `REGISTRY` -- every caller of `flag()`
    is expected to pass a registered key, and a typo here should fail loud
    rather than silently always resolving to its literal `env_default`.
    """
    if key not in _BY_KEY:
        raise KeyError(f"unknown feature flag: {key!r}")
    with _cache_lock:
        if time.monotonic() >= _cache_expires_at or key not in _cache:
            _refresh_cache_locked()
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
    global _cache, _cache_expires_at
    with _cache_lock:
        _cache = {}
        _cache_expires_at = 0.0


# Reuse pipeline.cache's existing "clear every cache" registry (wired into
# the per-test autouse fixture and the perf-debug reset endpoint) so this
# module's cache is swept alongside every other one, without every caller
# needing to know this module exists.
_REGISTERED_CLEARS.append(invalidate)
