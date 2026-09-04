import json
import types

import pytest

from api.middleware.ratelimit import (
    ASK_ANON_QUOTA_EXCEEDED_CODE,
    FREE_LIMIT,
    PRO_LIMIT,
    AnonQuotaContext,
    _key_func,
    anon_quota_enabled,
    ask_anon_daily_limit,
    ask_anon_ip_daily_limit,
    ask_quota_exceeded_handler,
    check_and_consume_anon_quota,
    copilot_anon_daily_limit,
    get_or_issue_anon_session,
    reset_anon_quota_for_tests,
)


def test_free_limit_constant():
    assert FREE_LIMIT == "60/minute"


def test_pro_limit_constant():
    assert PRO_LIMIT == "600/minute"


def test_key_func_free_tier_uses_ip():
    from unittest.mock import MagicMock

    request = MagicMock()
    request.state.tier = "free"
    request.headers = {}
    request.client.host = "1.2.3.4"
    # get_remote_address returns client.host for non-proxied requests
    # We just test that it doesn't use "pro:" prefix for free tier
    key = _key_func(request)
    assert not key.startswith("pro:")


def test_key_func_pro_tier_uses_api_key():
    from unittest.mock import MagicMock

    request = MagicMock()
    request.state.tier = "pro"
    request.headers.get = lambda k, default=None: "my-api-key" if k == "X-API-Key" else default
    key = _key_func(request)
    assert key == "pro:my-api-key"


# ---------------------------------------------------------------------------
# Anonymous Ask LLM-call daily quota
# ---------------------------------------------------------------------------


def setup_function(_):
    """Every test in this module starts from a clean anon-quota bucket state,
    since the module-level in-memory storage would otherwise leak counts
    between tests."""
    reset_anon_quota_for_tests()


def test_ask_anon_daily_limit_default():
    assert ask_anon_daily_limit() == 5


def test_ask_anon_ip_daily_limit_default():
    assert ask_anon_ip_daily_limit() == 20


def test_ask_anon_daily_limit_reads_env_live(monkeypatch):
    monkeypatch.setenv("ASK_ANON_DAILY_LIMIT", "7")
    assert ask_anon_daily_limit() == 7


def test_anon_quota_enabled_default_true(monkeypatch):
    monkeypatch.delenv("ASK_ANON_QUOTA_ENABLED", raising=False)
    assert anon_quota_enabled() is True


def test_anon_quota_enabled_false_variants(monkeypatch):
    for off in ("false", "0", "no", "FALSE", "  False  "):
        monkeypatch.setenv("ASK_ANON_QUOTA_ENABLED", off)
        assert anon_quota_enabled() is False, off


def test_check_and_consume_allows_up_to_the_daily_limit(monkeypatch):
    monkeypatch.setenv("ASK_ANON_DAILY_LIMIT", "3")
    monkeypatch.setenv("ASK_ANON_IP_DAILY_LIMIT", "100")
    for _ in range(3):
        assert check_and_consume_anon_quota("session-a", "9.9.9.9") is True
    # The 4th call in the same day for the same session is over budget.
    assert check_and_consume_anon_quota("session-a", "9.9.9.9") is False


def test_check_and_consume_is_scoped_per_session(monkeypatch):
    """A different anon-session key gets its own independent budget."""
    monkeypatch.setenv("ASK_ANON_DAILY_LIMIT", "1")
    monkeypatch.setenv("ASK_ANON_IP_DAILY_LIMIT", "100")
    assert check_and_consume_anon_quota("session-a", "9.9.9.9") is True
    assert check_and_consume_anon_quota("session-a", "9.9.9.9") is False
    # Different session, same IP (still under the loose IP backstop) — fresh budget.
    assert check_and_consume_anon_quota("session-b", "9.9.9.9") is True


def test_check_and_consume_ip_backstop_blocks_wholesale_abuse(monkeypatch):
    """Many distinct anon-session keys from one IP still hit the IP ceiling."""
    monkeypatch.setenv("ASK_ANON_DAILY_LIMIT", "100")
    monkeypatch.setenv("ASK_ANON_IP_DAILY_LIMIT", "2")
    assert check_and_consume_anon_quota("session-1", "5.5.5.5") is True
    assert check_and_consume_anon_quota("session-2", "5.5.5.5") is True
    # A third distinct session from the same IP is blocked by the backstop
    # even though session-3's own per-session budget is untouched.
    assert check_and_consume_anon_quota("session-3", "5.5.5.5") is False


def test_check_and_consume_declined_call_consumes_neither_bucket(monkeypatch):
    """A call blocked by the IP backstop must not burn down the requesting
    session's own budget — otherwise a request that was refused would still
    cost the caller one of their daily questions."""
    monkeypatch.setenv("ASK_ANON_DAILY_LIMIT", "1")
    monkeypatch.setenv("ASK_ANON_IP_DAILY_LIMIT", "1")
    assert check_and_consume_anon_quota("session-a", "1.1.1.1") is True
    # Blocked by the IP backstop (already at 1/1), not by session-b's own budget.
    assert check_and_consume_anon_quota("session-b", "1.1.1.1") is False
    # session-b's own bucket was never touched by the blocked attempt above —
    # on a fresh IP it still has its full daily allowance.
    assert check_and_consume_anon_quota("session-b", "2.2.2.2") is True


def test_check_and_consume_kill_switch_disabled_never_blocks(monkeypatch):
    monkeypatch.setenv("ASK_ANON_QUOTA_ENABLED", "false")
    monkeypatch.setenv("ASK_ANON_DAILY_LIMIT", "1")
    monkeypatch.setenv("ASK_ANON_IP_DAILY_LIMIT", "1")
    for _ in range(5):
        assert check_and_consume_anon_quota("session-a", "1.1.1.1") is True


def test_ask_and_copilot_scopes_have_independent_buckets(monkeypatch):
    monkeypatch.setenv("ASK_ANON_DAILY_LIMIT", "1")
    monkeypatch.setenv("COPILOT_ANON_DAILY_LIMIT", "1")
    monkeypatch.setenv("ASK_ANON_IP_DAILY_LIMIT", "100")
    monkeypatch.setenv("COPILOT_ANON_IP_DAILY_LIMIT", "100")
    reset_anon_quota_for_tests()

    assert check_and_consume_anon_quota("sess-1", "1.2.3.4", scope="ask") is True
    assert check_and_consume_anon_quota("sess-1", "1.2.3.4", scope="ask") is False
    # exhausting "ask" for this session must not affect the independent "copilot" bucket
    assert check_and_consume_anon_quota("sess-1", "1.2.3.4", scope="copilot") is True


def test_copilot_anon_daily_limit_reads_own_env_var(monkeypatch):
    monkeypatch.setenv("COPILOT_ANON_DAILY_LIMIT", "42")
    assert copilot_anon_daily_limit() == 42


class _FakeRequest:
    def __init__(self, cookies=None):
        self.cookies = cookies or {}


class _FakeResponse:
    def __init__(self):
        self.set_cookie_calls = []

    def set_cookie(self, key, value, **kwargs):
        self.set_cookie_calls.append((key, value, kwargs))


def test_get_or_issue_anon_session_mints_cookie_when_absent():
    request = _FakeRequest()
    response = _FakeResponse()
    sid = get_or_issue_anon_session(request, response)
    assert isinstance(sid, str) and sid
    assert len(response.set_cookie_calls) == 1
    _name, _value, kwargs = response.set_cookie_calls[0]
    assert kwargs["httponly"] is True
    assert kwargs["samesite"] == "lax"


def test_get_or_issue_anon_session_reuses_valid_existing_cookie():
    from api.middleware.ratelimit import ASK_ANON_SESSION_COOKIE_NAME, _anon_session_signer

    # Issue once to get a realistic signed cookie value.
    first_request = _FakeRequest()
    first_response = _FakeResponse()
    sid = get_or_issue_anon_session(first_request, first_response)
    _name, signed_value, _kwargs = first_response.set_cookie_calls[0]

    # A second request presenting that same signed cookie must resolve to the
    # SAME session id, and must not re-issue a cookie.
    second_request = _FakeRequest(cookies={ASK_ANON_SESSION_COOKIE_NAME: signed_value})
    second_response = _FakeResponse()
    sid2 = get_or_issue_anon_session(second_request, second_response)
    assert sid2 == sid
    assert second_response.set_cookie_calls == []
    # Sanity: the raw cookie really does verify against the module's signer.
    assert _anon_session_signer.loads(signed_value) == sid


def test_get_or_issue_anon_session_falls_back_on_tampered_cookie():
    from api.middleware.ratelimit import ASK_ANON_SESSION_COOKIE_NAME

    request = _FakeRequest(cookies={ASK_ANON_SESSION_COOKIE_NAME: "not-a-real-signed-token"})
    response = _FakeResponse()
    sid = get_or_issue_anon_session(request, response)
    assert isinstance(sid, str) and sid
    # Falls back sanely: mints and sets a fresh cookie instead of raising.
    assert len(response.set_cookie_calls) == 1


def test_anon_quota_context_holds_both_keys():
    ctx = AnonQuotaContext(session_key="s", ip_key="1.2.3.4")
    assert ctx.session_key == "s"
    assert ctx.ip_key == "1.2.3.4"


def _fake_request(locale="ja"):
    # get_locale reads request.state.locale; a SimpleNamespace is enough
    # (mirrors tests/unit/test_aggregate_errors.py's identical helper).
    return types.SimpleNamespace(state=types.SimpleNamespace(locale=locale))


@pytest.mark.asyncio
async def test_ask_quota_exceeded_handler_returns_429_with_machine_code():
    resp = await ask_quota_exceeded_handler(_fake_request("en"), Exception())
    assert resp.status_code == 429
    body = json.loads(resp.body)
    assert body["code"] == ASK_ANON_QUOTA_EXCEEDED_CODE
    assert body["detail"]  # non-empty, user-facing


@pytest.mark.asyncio
async def test_ask_quota_exceeded_handler_localized_detail():
    en = json.loads((await ask_quota_exceeded_handler(_fake_request("en"), Exception())).body)["detail"]
    ja = json.loads((await ask_quota_exceeded_handler(_fake_request("ja"), Exception())).body)["detail"]
    assert en != ja


@pytest.mark.asyncio
async def test_ask_quota_exceeded_handler_unknown_locale_falls_back_to_ja():
    body = json.loads((await ask_quota_exceeded_handler(_fake_request("fr"), Exception())).body)
    ja_body = json.loads((await ask_quota_exceeded_handler(_fake_request("ja"), Exception())).body)
    assert body["detail"] == ja_body["detail"]
