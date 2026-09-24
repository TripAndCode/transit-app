"""Pure-logic tests for `pipeline.flags` -- the DB-backed feature-flag
registry with an env-var fallback. Lives under `tests/unit/` per CLAUDE.md's
"pure logic tests bypass DB fixtures" convention: every test here forces the
DB read path to fail (an unreachable `DATABASE_URL`, or none at all) so the
env fallback is what's actually exercised, never a real Postgres.
"""

from __future__ import annotations

import time

import pytest

from pipeline import flags


@pytest.fixture(autouse=True)
def _reset_flags_cache(monkeypatch):
    """Every test starts with a cold cache and no real DB to reach.

    `DATABASE_URL` points at a port nothing listens on, so `flags._load_all`
    fails fast (connection refused) and falls back to env defaults --
    exactly the "DB unreachable" contract this module promises.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/nonexistent")
    flags.invalidate()
    yield
    flags.invalidate()


def test_flag_returns_env_default_when_db_unreachable(monkeypatch):
    monkeypatch.delenv("ASK_ROUTER_ENABLED", raising=False)
    assert flags.flag("ask_router_enabled", True) is True


def test_flag_reads_env_var_over_env_default(monkeypatch):
    monkeypatch.setenv("ASK_FOLLOWUP_ENABLED", "true")
    assert flags.flag("ask_followup_enabled", False) is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " true "])
def test_flag_env_var_truthy_values(monkeypatch, value):
    monkeypatch.setenv("COPILOT_INSIGHT_ENABLED", value)
    assert flags.flag("copilot_insight_enabled", False) is True


@pytest.mark.parametrize("value", ["0", "false", "no", "", "maybe"])
def test_flag_env_var_falsy_values(monkeypatch, value):
    monkeypatch.setenv("COPILOT_INSIGHT_ENABLED", value)
    assert flags.flag("copilot_insight_enabled", True) is False


def test_flag_unknown_key_raises():
    with pytest.raises(KeyError):
        flags.flag("not_a_real_flag", False)


def test_flag_no_database_url_falls_back_to_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PERF_DEBUG_ENABLED", "true")
    assert flags.flag("perf_debug_enabled", False) is True


def test_get_flag_state_reports_env_source_when_no_override(monkeypatch):
    monkeypatch.setenv("ASK_HISTORY_ENABLED", "false")
    state = flags.get_flag_state("ask_history_enabled")
    assert state.value is False
    assert state.source == "env"
    assert state.env_default is False  # actual env resolution, not the registry's unset-fallback literal
    assert state.updated_by is None
    assert state.reason is None


def test_cache_is_reused_within_ttl(monkeypatch):
    """A second call within the TTL window must not re-hit the DB -- verified
    indirectly: changing the env var mid-window must NOT change the answer,
    because the cached state (not a fresh env read) is what's returned."""
    monkeypatch.setenv("ASK_QUERY_LOG_ENABLED", "true")
    first = flags.flag("ask_query_log_enabled", True)
    monkeypatch.setenv("ASK_QUERY_LOG_ENABLED", "false")
    second = flags.flag("ask_query_log_enabled", True)
    assert first == second is True


def test_an_expired_cache_refreshes_behind_the_reader(monkeypatch):
    """`flag()` is called from async request handlers, so an expired window
    must not make the caller wait on the database. The read that finds the
    cache stale gets the old value and starts the refresh behind it."""
    monkeypatch.setenv("ASK_INTENT_CACHE_ENABLED", "true")
    assert flags.flag("ask_intent_cache_enabled", False) is True

    real_monotonic = time.monotonic
    monkeypatch.setattr(flags.time, "monotonic", lambda: real_monotonic() + flags._CACHE_TTL_SECONDS + 1)
    monkeypatch.setenv("ASK_INTENT_CACHE_ENABLED", "false")
    assert flags.flag("ask_intent_cache_enabled", False) is True, "the reader waited on the refresh"

    refresh = flags._refresh_thread
    assert refresh is not None
    refresh.join(timeout=5)
    assert flags.flag("ask_intent_cache_enabled", False) is False


def test_invalidate_forces_immediate_reread(monkeypatch):
    monkeypatch.setenv("WEATHER_INGEST_ENABLED", "true")
    assert flags.flag("weather_ingest_enabled", False) is True

    monkeypatch.setenv("WEATHER_INGEST_ENABLED", "false")
    flags.invalidate()
    assert flags.flag("weather_ingest_enabled", False) is False


def test_registry_covers_every_known_gated_env_var():
    """Regression guard for the nine kill switches this task registers --
    a name dropped from the registry silently stops being overridable from
    the admin UI (the call site would still work off its env default, but a
    PATCH against it would 404/never apply)."""
    expected = {
        "ASK_ROUTER_ENABLED",
        "ASK_FOLLOWUP_ENABLED",
        "COPILOT_INSIGHT_ENABLED",
        "ASK_HISTORY_ENABLED",
        "ASK_INTENT_CACHE_ENABLED",
        "ASK_QUERY_LOG_ENABLED",
        "WEATHER_INGEST_ENABLED",
        "OPENAPI_DOCS_ENABLED",
        "PERF_DEBUG_ENABLED",
    }
    assert {d.env_var for d in flags.REGISTRY} == expected


def test_registry_keys_are_unique():
    keys = [d.key for d in flags.REGISTRY]
    assert len(keys) == len(set(keys))


def test_registry_env_defaults_match_prior_hardcoded_defaults():
    """Locks each flag's fallback to the literal default its call site used
    to pass to `os.environ.get(...)` before this task -- a silent flip here
    would change production behavior for every deployment that has never
    set the env var and never touched the admin UI."""
    expected_defaults = {
        "ask_router_enabled": True,
        "ask_followup_enabled": False,
        "copilot_insight_enabled": False,
        "ask_history_enabled": True,
        "ask_intent_cache_enabled": False,
        "ask_query_log_enabled": True,
        "weather_ingest_enabled": False,
        "openapi_docs_enabled": False,
        "perf_debug_enabled": False,
    }
    assert {d.key: d.env_default for d in flags.REGISTRY} == expected_defaults


def test_registry_label_keys_are_i18n_keys_not_literal_text():
    """Labels are i18n keys the frontend resolves via `t()`, never literal
    Japanese/English strings baked into the API response."""
    for definition in flags.REGISTRY:
        assert definition.label_key.startswith("admin.flags.")


def test_an_override_survives_a_database_read_failure(monkeypatch):
    """A kill switch is used during an incident, which is exactly when the
    database may also be unwell. Losing the override then would switch the
    feature back on at the worst possible moment."""
    monkeypatch.setenv("ASK_INTENT_CACHE_ENABLED", "true")
    monkeypatch.setattr(flags, "_load_overrides", lambda: {"ask_intent_cache_enabled": (False, "incident", 1, None)})
    flags.invalidate()
    assert flags.flag("ask_intent_cache_enabled", False) is False

    monkeypatch.setattr(flags, "_load_overrides", lambda: None)
    flags.invalidate()
    state = flags.get_flag_state("ask_intent_cache_enabled")
    assert state.value is False, "the override was lost when the read failed"
    assert state.source == "override"


def test_an_env_change_still_lands_while_the_database_is_unreadable(monkeypatch):
    """Only overrides are held back. A flag nobody has overridden keeps
    following its env var, so a local toggle is not silently inert."""
    monkeypatch.setattr(flags, "_load_overrides", lambda: None)
    monkeypatch.setenv("WEATHER_INGEST_ENABLED", "true")
    flags.invalidate()
    assert flags.flag("weather_ingest_enabled", False) is True

    monkeypatch.setenv("WEATHER_INGEST_ENABLED", "false")
    flags.invalidate()
    assert flags.flag("weather_ingest_enabled", False) is False


def test_a_missing_table_is_not_treated_as_a_read_failure(monkeypatch):
    """A fresh deployment pending the migration genuinely has no overrides,
    so env is the right answer rather than something to hold onto."""
    monkeypatch.setattr(flags, "_load_overrides", lambda: {})
    monkeypatch.setenv("WEATHER_INGEST_ENABLED", "true")
    flags.invalidate()
    assert flags.get_flag_state("weather_ingest_enabled").source == "env"


def test_an_unrecognised_env_value_keeps_the_flags_own_default(monkeypatch):
    """The default-on switches were previously read as "off only when the
    value is exactly false", so an unrecognised value must not disable
    them -- a deployment using `on` or `enabled` would go dark."""
    monkeypatch.setenv("ASK_QUERY_LOG_ENABLED", "on")
    flags.invalidate()
    assert flags.flag("ask_query_log_enabled", True) is True

    monkeypatch.setenv("ASK_QUERY_LOG_ENABLED", "false")
    flags.invalidate()
    assert flags.flag("ask_query_log_enabled", True) is False

    monkeypatch.setenv("ASK_INTENT_CACHE_ENABLED", "nonsense")
    flags.invalidate()
    assert flags.flag("ask_intent_cache_enabled", False) is False


def test_a_slow_refresh_does_not_block_readers(monkeypatch):
    """`flag()` is called from async request handlers, so a refresh must
    never be something a caller waits on -- not even by way of the lock the
    refresh holds while it swaps its result in."""
    import threading

    refresh_seconds = 0.3

    def slow_load():
        time.sleep(refresh_seconds)
        return {"ask_intent_cache_enabled": (False, "held", 1, None)}

    monkeypatch.setattr(flags, "_load_overrides", slow_load)
    monkeypatch.setattr(flags, "_CACHE_TTL_SECONDS", 0.05)
    flags.warm()

    slowest = 0.0

    def reader():
        nonlocal slowest
        for _ in range(20):
            started = time.monotonic()
            flags.flag("ask_intent_cache_enabled", True)
            slowest = max(slowest, time.monotonic() - started)
            time.sleep(0.01)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert all(not t.is_alive() for t in threads)
    assert slowest < refresh_seconds / 3, f"a reader waited {slowest * 1000:.0f}ms on the refresh"

    if flags._refresh_thread is not None:
        flags._refresh_thread.join(timeout=5)


def test_a_refresh_that_began_before_a_write_cannot_overwrite_it(monkeypatch):
    """A background refresh started by an expiry can still be reading when
    an admin PATCH lands. Finishing last must not make it win: its data
    predates the write, and the PATCH is promised the next read sees the
    new value."""
    key = "ask_intent_cache_enabled"
    calls = {"n": 0}

    def staged_load():
        calls["n"] += 1
        if calls["n"] == 1:
            time.sleep(0.4)
            return {key: (True, "before-the-write", 1, None)}
        return {key: (False, "after-the-write", 1, None)}

    monkeypatch.setattr(flags, "_load_overrides", lambda: {})
    flags.warm()
    monkeypatch.setattr(flags, "_load_overrides", staged_load)

    flags._cache_expires_at = 0.0
    flags.flag(key, True)  # starts the slow pre-write read in the background
    time.sleep(0.05)

    flags.invalidate()  # the PATCH
    assert flags.get_flag_state(key).reason == "after-the-write"

    if flags._refresh_thread is not None:
        flags._refresh_thread.join(timeout=5)
    state = flags.get_flag_state(key)
    assert state.reason == "after-the-write", "a refresh that predates the write overwrote it"
    assert state.value is False


def test_a_superseded_first_refresh_resolves_from_env_rather_than_raising(monkeypatch):
    """A refresh discards its result when a write supersedes it. If that
    happens to the very first refresh in a process, nothing has ever been
    cached -- and `flag()` is documented never to raise, because it is read
    from request paths as a kill switch."""
    monkeypatch.setattr(flags, "_cache", {})
    monkeypatch.setattr(flags, "_committed_seq", 0)

    def load_then_supersede():
        # Stand in for an admin PATCH landing while this read is in flight.
        flags.invalidate()
        return {}

    monkeypatch.setattr(flags, "_load_overrides", load_then_supersede)
    monkeypatch.setenv("WEATHER_INGEST_ENABLED", "true")

    state = flags.get_flag_state("weather_ingest_enabled")
    assert state.value is True
    assert state.source == "env"
