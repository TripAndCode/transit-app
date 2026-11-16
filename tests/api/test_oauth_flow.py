"""End-to-end OAuth callback + logout coverage with Authlib's create_client
patched out. We never hit a real provider; the userinfo path is monkeypatched
to return canned dicts. Sign-in admits only administrators, so a test that
expects a session either seeds an admin account or names the address in
``ADMIN_EMAILS``.

Each test builds a valid signed ``oauth_tx`` cookie via ``auth_mod._get_signer().dumps``
so the callback's state check accepts it, then asserts redirect target +
database side effects (users, oauth_identities, sessions, login_events).
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import httpx
import pytest
from httpx import ASGITransport

from api.security import token_hash
from tests.conftest import _test_pool
from tests.fixtures.users import seed_session


@pytest.fixture(autouse=True)
def _set_oauth_env(monkeypatch):
    """Stuff env vars that auth.py reads live (os.environ.get at call time —
    _require_sso_configured, PUBLIC_BASE_URL, etc.), so plain monkeypatch.setenv
    is enough with no reload.

    ADMIN_EMAILS is the one exception: it's built at import-time as a frozen
    set, so monkeypatch.setenv("ADMIN_EMAILS", ...) alone wouldn't change it.
    Tests that need a different set monkeypatch ``auth_mod.ADMIN_EMAILS``
    directly instead (see test_admin_email_promotes) — cheaper and more
    explicit than reloading the whole module to re-freeze it.

    Do not importlib.reload(api.routers.auth) here. Nothing needs it
    (everything else either reads env live already, per above, or — like the
    oauth-tx signer — is fine staying frozen at whatever it was on first
    import, since every test round-trips through the same frozen instance),
    and a reload re-runs auth.py's `@limiter.limit(...)` decorators,
    registering a duplicate rate-limit rule against the shared slowapi
    Limiter singleton each time. Those accumulate across tests and silently
    starve a rate-limited auth route's quota in a DIFFERENT test file's tests
    whenever both run in the same session.
    """
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "g")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gs")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "h")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "hs")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://test")
    monkeypatch.delenv("FRONTEND_BASE_URL", raising=False)
    monkeypatch.setenv("ADMIN_EMAILS", "")
    monkeypatch.setenv("SESSION_SIGNING_KEY", "test-signing-key")
    yield


@pytest.fixture
async def auth_client(apply_schema):
    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await pool.close()


@pytest.mark.asyncio
async def test_callback_with_bad_state_redirects_to_error(auth_client):
    resp = await auth_client.get(
        "/api/auth/google/callback?code=abc&state=mismatch",
        cookies={"oauth_tx": "garbage"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/login?error=state" in resp.headers["location"]


@pytest.mark.asyncio
async def test_callback_unverified_email_redirects(auth_client, aconn, monkeypatch):
    """Mock the userinfo path to return verified=False."""
    from api.routers import auth as auth_mod

    async def fake_userinfo(client, token, provider):
        return {"sub": "x", "email": "a@x", "email_verified": False, "name": "A", "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)

    # build a valid tx cookie ourselves
    payload = auth_mod._get_signer().dumps({"state": "s", "verifier": "v", "next": "/", "provider": "google"})

    # mock authorize_access_token to skip real provider call
    fake_token = {"access_token": "tok"}
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value=fake_token)
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        resp = await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert "error=unverified_email" in resp.headers["location"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reported", ["yo@x", "Yo@X"])
async def test_callback_creates_an_admin_emails_account_and_session(auth_client, aconn, monkeypatch, reported):
    from api.routers import auth as auth_mod

    monkeypatch.setattr(auth_mod, "ADMIN_EMAILS", {"yo@x"})

    async def fake_userinfo(client, token, provider):
        return {
            "sub": "google-sub-1",
            "email": reported,
            "email_verified": True,
            "name": "Yo",
            "avatar_url": "http://a/x.png",
        }

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._get_signer().dumps({"state": "s", "verifier": "v", "next": "/", "provider": "google"})
    fake_token = {"access_token": "tok"}
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value=fake_token)
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        resp = await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://test/"
    assert "sid=" in resp.headers.get("set-cookie", "")

    row = await aconn.fetchrow("SELECT user_id, email, role FROM users WHERE email='yo@x'")
    assert row is not None
    assert row["role"] == "admin"
    s = await aconn.fetchrow("SELECT sid_hash FROM sessions WHERE user_id=$1", row["user_id"])
    assert s is not None


@pytest.mark.asyncio
async def test_successful_login_redirects_to_the_frontend_origin_when_it_differs(auth_client, monkeypatch):
    """In local dev the SPA runs on Vite's own dev-server port, separate from
    this API, so the post-login Location header must name that origin; a
    bare relative path would resolve against this API and land on a route
    only the SPA serves."""
    from api.routers import auth as auth_mod

    monkeypatch.setenv("FRONTEND_BASE_URL", "http://localhost:5173")
    monkeypatch.setattr(auth_mod, "ADMIN_EMAILS", {"dev@x"})

    async def fake_userinfo(client, token, provider):
        return {
            "sub": "google-sub-dev",
            "email": "dev@x",
            "email_verified": True,
            "name": "Dev",
            "avatar_url": None,
        }

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._get_signer().dumps(
        {"state": "s", "verifier": "v", "next": "/agencies/1/ask", "provider": "google"}
    )
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "tok"})
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        resp = await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://localhost:5173/agencies/1/ask"


@pytest.mark.asyncio
async def test_failed_login_redirects_to_the_frontend_origin_when_it_differs(auth_client, monkeypatch):
    """Failure redirects back to ``/login`` carry the frontend origin too, or
    a failed dev sign-in strands the browser on the backend."""
    monkeypatch.setenv("FRONTEND_BASE_URL", "http://localhost:5173")

    resp = await auth_client.get(
        "/api/auth/google/callback?code=abc&state=mismatch",
        cookies={"oauth_tx": "garbage"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://localhost:5173/login?error=state"


@pytest.mark.asyncio
async def test_admin_email_promotes(auth_client, aconn, monkeypatch):
    from api.routers import auth as auth_mod

    monkeypatch.setattr(auth_mod, "ADMIN_EMAILS", {"boss@x"})

    async def fake_userinfo(client, token, provider):
        return {"sub": "g2", "email": "boss@x", "email_verified": True, "name": "Boss", "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._get_signer().dumps({"state": "s", "verifier": "v", "next": "/", "provider": "google"})
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "t"})
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        resp = await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    assert resp.headers["location"] == "http://test/"
    row = await aconn.fetchrow("SELECT user_id, role FROM users WHERE email='boss@x'")
    assert row["role"] == "admin"
    assert await aconn.fetchval("SELECT count(*) FROM sessions WHERE user_id=$1", row["user_id"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reported", ["shared@x", "Shared@X"])
async def test_account_linking_by_verified_email(auth_client, aconn, monkeypatch, reported):
    """An admin exists from Google login; the same verified email via GitHub
    auto-links to that admin account and signs in, in whatever case the second
    provider reports it."""
    uid = await aconn.fetchval(
        "INSERT INTO users (email, name, role) VALUES ('shared@x', 'A', 'admin') RETURNING user_id"
    )
    await aconn.execute(
        "INSERT INTO oauth_identities (provider, provider_sub, user_id, email_at_link) "
        "VALUES ('google', 'g-sub', $1, 'shared@x')",
        uid,
    )

    from api.routers import auth as auth_mod

    async def fake_userinfo(client, token, provider):
        return {"sub": "gh-sub", "email": reported, "email_verified": True, "name": "A", "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._get_signer().dumps({"state": "s", "verifier": "v", "next": "/", "provider": "github"})
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "t"})
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        resp = await auth_client.get(
            "/api/auth/github/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    assert resp.headers["location"] == "http://test/"
    assert await aconn.fetchval("SELECT count(*) FROM sessions WHERE user_id=$1", uid) == 1
    rows = await aconn.fetch(
        "SELECT provider FROM oauth_identities WHERE user_id=$1 ORDER BY provider",
        uid,
    )
    assert [r["provider"] for r in rows] == ["github", "google"]
    n_users = await aconn.fetchval("SELECT count(*) FROM users WHERE email='shared@x'")
    assert n_users == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reported", ["root@local", "Root@Local"])
async def test_oauth_login_refuses_to_take_over_a_local_password_account(auth_client, aconn, monkeypatch, reported):
    """A break-glass/local-password account (password_hash set, no linked
    OAuth identity) must not be silently claimed by an OAuth login whose
    verified email happens to match — that would hand whoever controls the
    email address a live admin session, the same takeover class
    seed_local_admin's OAuth-linked guard closes in the other direction."""
    uid = (
        await aconn.fetchrow(
            "INSERT INTO users (email, name, role, password_hash) "
            "VALUES ('root@local', 'Local Admin', 'admin', 'hash') RETURNING user_id"
        )
    )["user_id"]

    from api.routers import auth as auth_mod

    async def fake_userinfo(client, token, provider):
        return {"sub": "g-sub", "email": reported, "email_verified": True, "name": "Someone", "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._get_signer().dumps({"state": "s", "verifier": "v", "next": "/", "provider": "google"})
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "t"})
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        resp = await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert "error=" in resp.headers["location"]
    # No OAuth identity got linked to the local account, and no session was minted for it.
    n_identities = await aconn.fetchval("SELECT count(*) FROM oauth_identities WHERE user_id=$1", uid)
    assert n_identities == 0
    n_sessions = await aconn.fetchval("SELECT count(*) FROM sessions WHERE user_id=$1", uid)
    assert n_sessions == 0


@pytest.mark.asyncio
async def test_first_login_emits_account_created(auth_client, aconn, monkeypatch):
    """Brand-new ADMIN_EMAILS account: account_created fires once, then the
    promotion and the login."""
    from api.routers import auth as auth_mod

    monkeypatch.setattr(auth_mod, "ADMIN_EMAILS", {"new@x"})

    async def fake_userinfo(client, token, provider):
        return {"sub": "new-sub", "email": "new@x", "email_verified": True, "name": "N", "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._get_signer().dumps({"state": "s", "verifier": "v", "next": "/", "provider": "google"})
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "t"})
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    uid = await aconn.fetchval("SELECT user_id FROM users WHERE email='new@x'")
    kinds = [
        r["kind"] for r in await aconn.fetch("SELECT kind FROM login_events WHERE user_id=$1 ORDER BY event_id", uid)
    ]
    assert kinds == ["account_created", "role_changed", "login"]


@pytest.mark.asyncio
async def test_login_event_and_session_fields_on_successful_callback(auth_client, aconn, monkeypatch):
    """Field-level characterization of the shared `_mint_session_and_log_login`
    sequence, exercised here via the OAuth callback()."""
    from api.routers import auth as auth_mod

    monkeypatch.setattr(auth_mod, "ADMIN_EMAILS", {"field@x"})

    async def fake_userinfo(client, token, provider):
        return {"sub": "field-sub", "email": "field@x", "email_verified": True, "name": "F", "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._get_signer().dumps({"state": "s", "verifier": "v", "next": "/", "provider": "google"})
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "t"})
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        resp = await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            headers={"user-agent": "test-ua"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    uid = await aconn.fetchval("SELECT user_id FROM users WHERE email='field@x'")
    row = await aconn.fetchrow(
        "SELECT user_id, actor_id, kind, provider, user_agent, meta FROM login_events "
        "WHERE user_id=$1 AND kind='login'",
        uid,
    )
    assert row is not None
    assert row["user_id"] == uid
    assert row["actor_id"] == uid
    assert row["provider"] == "google"
    assert row["user_agent"] == "test-ua"
    assert row["meta"] is None
    stored = await aconn.fetchrow("SELECT sid, sid_hash FROM sessions WHERE user_id=$1", uid)
    assert stored is not None
    # The raw session id lives only in the cookie; the row keeps its digest.
    assert stored["sid"] is None
    cookie_sid = resp.headers["set-cookie"].split("sid=", 1)[1].split(";", 1)[0]
    assert stored["sid_hash"] == token_hash(cookie_sid)


@pytest.mark.asyncio
async def test_repeat_login_skips_account_created(auth_client, aconn, monkeypatch):
    """An admin's second login from the same provider signs in through the
    linked identity and only emits ``login``, not account_created."""
    uid = (await aconn.fetchrow("INSERT INTO users (email, role) VALUES ('repeat@x', 'admin') RETURNING user_id"))[
        "user_id"
    ]
    await aconn.execute(
        "INSERT INTO oauth_identities (provider, provider_sub, user_id, email_at_link) "
        "VALUES ('google', 'r-sub', $1, 'repeat@x')",
        uid,
    )

    from api.routers import auth as auth_mod

    async def fake_userinfo(client, token, provider):
        return {"sub": "r-sub", "email": "repeat@x", "email_verified": True, "name": None, "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._get_signer().dumps({"state": "s", "verifier": "v", "next": "/", "provider": "google"})
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "t"})
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        resp = await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    assert resp.headers["location"] == "http://test/"
    assert await aconn.fetchval("SELECT count(*) FROM sessions WHERE user_id=$1", uid) == 1
    kinds = [
        r["kind"] for r in await aconn.fetch("SELECT kind FROM login_events WHERE user_id=$1 ORDER BY event_id", uid)
    ]
    assert kinds == ["login"]


@pytest.mark.asyncio
async def test_bad_state_records_login_failed(auth_client, aconn):
    """Missing tx cookie + mismatched state → login_failed audit row naming
    the reason only: no account, address or browser."""
    resp = await auth_client.get(
        "/api/auth/google/callback?code=abc&state=mismatch",
        cookies={"oauth_tx": "garbage"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    row = await aconn.fetchrow(
        "SELECT kind, provider, user_id, ip, user_agent, meta::text AS meta FROM login_events "
        "WHERE kind='login_failed' ORDER BY event_id DESC LIMIT 1"
    )
    assert row is not None
    assert row["provider"] == "google"
    assert json.loads(row["meta"]) == {"reason": "state"}
    assert (row["user_id"], row["ip"], row["user_agent"]) == (None, None, None)


@pytest.mark.asyncio
async def test_unverified_email_records_login_failed(auth_client, aconn, monkeypatch):
    from api.routers import auth as auth_mod

    async def fake_userinfo(client, token, provider):
        return {"sub": "u", "email": "u@x", "email_verified": False, "name": None, "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._get_signer().dumps({"state": "s", "verifier": "v", "next": "/", "provider": "google"})
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "t"})
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    row = await aconn.fetchrow(
        "SELECT ip, user_agent, meta::text AS meta FROM login_events WHERE kind='login_failed' "
        "ORDER BY event_id DESC LIMIT 1"
    )
    assert row is not None
    assert json.loads(row["meta"]) == {"reason": "unverified_email"}
    assert (row["ip"], row["user_agent"]) == (None, None)


@pytest.mark.asyncio
async def test_logout_when_anonymous_still_204(auth_client):
    """A user whose cookie already expired/cleared should not see a 401
    on POST /api/auth/logout — the endpoint is idempotent."""
    resp = await auth_client.post("/api/auth/logout", headers={"Origin": "http://test"})
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_logout_deletes_session(auth_client, aconn):
    sid = (await seed_session(aconn, email="x@x")).sid
    resp = await auth_client.post(
        "/api/auth/logout",
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert resp.status_code == 204
    n = await aconn.fetchval("SELECT count(*) FROM sessions WHERE sid_hash=$1", token_hash(sid))
    assert n == 0


@pytest.mark.asyncio
async def test_real_login_then_callback_does_not_raise_duplicate_code_verifier(auth_client, aconn, monkeypatch):
    """The callback must not pass ``code_verifier`` explicitly to
    ``authorize_access_token``: Authlib's own ``_format_state_params``
    already injects ``code_verifier`` from the Starlette session state that
    ``authorize_redirect`` (in /login) stored there, and passing both raises
    ``TypeError: got multiple values for keyword argument 'code_verifier'``
    on every real login attempt.

    Unlike the other tests in this file, this one does NOT mock
    ``authorize_access_token`` itself (that would mock away the exact bug).
    It drives the real /login endpoint first so the session is populated for
    real, then only stubs the network-bound token fetch one level deeper.
    Uses github (no OIDC discovery network call needed for authorize_redirect,
    unlike google's server_metadata_url config).
    """
    from api.routers import auth as auth_mod

    login_resp = await auth_client.get("/api/auth/github/login", follow_redirects=False)
    assert login_resp.status_code == 302
    tx_cookie = login_resp.cookies.get("oauth_tx")
    assert tx_cookie
    tx = auth_mod._get_signer().loads(tx_cookie)

    monkeypatch.setattr(auth_mod, "ADMIN_EMAILS", {"real@x"})

    async def fake_userinfo(client, token, provider):
        return {"sub": "real-sub", "email": "real@x", "email_verified": True, "name": "Real", "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)

    client = auth_mod.oauth.create_client("github")

    async def fake_fetch_access_token(**kwargs):
        return {"access_token": "tok"}

    monkeypatch.setattr(client, "fetch_access_token", fake_fetch_access_token)

    # oauth_tx + the Starlette session cookie both persist on auth_client's
    # own cookie jar from the /login response above — no need to pass them
    # explicitly (and doing so would duplicate the Cookie header).
    resp = await auth_client.get(
        f"/api/auth/github/callback?state={tx['state']}&code=c",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://test/"
    assert "error=" not in resp.headers["location"]


@pytest.mark.asyncio
async def test_a_github_error_body_fails_the_login_like_a_provider_outage(auth_client, aconn):
    """A rejected GitHub token answers `user` and `user/emails` with an error
    object. The callback sends the visitor back to /login and records the
    failure, the same as a failed token exchange."""
    from api.routers import auth as auth_mod

    payload = auth_mod._get_signer().dumps({"state": "s", "verifier": "v", "next": "/", "provider": "github"})
    error_body = MagicMock()
    error_body.json.return_value = {"message": "Bad credentials"}
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "t"})
    client_mock.get = AsyncMock(return_value=error_body)
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        resp = await auth_client.get(
            "/api/auth/github/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("http://test/login?error=provider_down")
    row = await aconn.fetchrow(
        "SELECT provider, meta::text AS meta FROM login_events WHERE kind='login_failed' ORDER BY event_id DESC LIMIT 1"
    )
    assert row is not None
    assert row["provider"] == "github"
    assert '"provider_down"' in row["meta"]


@pytest.mark.asyncio
async def test_provider_failure_keeps_next_for_the_retry(auth_client):
    """A sign-in that fails at token exchange sends the visitor back to /login
    with the page they were headed for, so their retry lands there."""
    from api.routers import auth as auth_mod

    destination = "/agencies/1/analysis?route=12"
    payload = auth_mod._get_signer().dumps({"state": "s", "verifier": "v", "next": destination, "provider": "google"})
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(side_effect=RuntimeError("provider down"))
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        resp = await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("http://test/login?error=provider_down&next=")
    assert location.endswith(quote(destination, safe=""))


async def _google_callback(auth_client, monkeypatch, info, next_url="/"):
    from api.routers import auth as auth_mod

    async def fake_userinfo(client, token, provider):
        return info

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._get_signer().dumps({"state": "s", "verifier": "v", "next": next_url, "provider": "google"})
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "t"})
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        return await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )


@pytest.mark.asyncio
async def test_a_suspended_account_is_refused_before_any_session_exists(auth_client, aconn, monkeypatch):
    """A suspended account gets no session, no cookie and no `login` event:
    the callback sends it back to /login with its own error instead, and the
    attempt is audited as a failed sign-in against that account, with the
    address and browser it came from."""
    uid = await aconn.fetchval(
        "INSERT INTO users (email, name, role, suspended_at)"
        " VALUES ('held@x', 'Held', 'admin', now()) RETURNING user_id"
    )
    await aconn.execute(
        "INSERT INTO oauth_identities (provider, provider_sub, user_id, email_at_link)"
        " VALUES ('google', 'held-sub', $1, 'held@x')",
        uid,
    )
    info = {"sub": "held-sub", "email": "held@x", "email_verified": True, "name": "Held", "avatar_url": None}

    resp = await _google_callback(auth_client, monkeypatch, info, next_url="/agencies/1")

    assert resp.status_code == 302
    assert resp.headers["location"] == "http://test/login?error=suspended&next=%2Fagencies%2F1"
    assert "sid=" not in resp.headers.get("set-cookie", "")
    assert await aconn.fetchval("SELECT count(*) FROM sessions WHERE user_id=$1", uid) == 0
    events = await aconn.fetch(
        "SELECT kind, actor_id, provider, meta->>'reason' AS reason, ip IS NOT NULL AS has_ip,"
        " user_agent IS NOT NULL AS has_ua FROM login_events WHERE user_id=$1",
        uid,
    )
    assert [dict(e) for e in events] == [
        {
            "kind": "login_failed",
            "actor_id": uid,
            "provider": "google",
            "reason": "suspended",
            "has_ip": True,
            "has_ua": True,
        }
    ]


@pytest.mark.asyncio
async def test_a_refused_sign_in_leaves_the_suspended_account_unchanged(auth_client, aconn, monkeypatch):
    """A suspended account reached through a new provider's verified email is
    neither linked to that identity nor promoted by ADMIN_EMAILS: the whole
    sign-in rolls back, leaving only the failure record."""
    from api.routers import auth as auth_mod

    monkeypatch.setattr(auth_mod, "ADMIN_EMAILS", {"held@x"})
    uid = await aconn.fetchval("INSERT INTO users (email, suspended_at) VALUES ('held@x', now()) RETURNING user_id")
    info = {"sub": "new-sub", "email": "Held@X", "email_verified": True, "name": "Held", "avatar_url": None}

    resp = await _google_callback(auth_client, monkeypatch, info)

    assert resp.headers["location"] == "http://test/login?error=suspended&next=%2F"
    assert await aconn.fetchval("SELECT count(*) FROM oauth_identities WHERE user_id=$1", uid) == 0
    assert await aconn.fetchval("SELECT role FROM users WHERE user_id=$1", uid) == "user"
    kinds = [r["kind"] for r in await aconn.fetch("SELECT kind FROM login_events WHERE user_id=$1", uid)]
    assert kinds == ["login_failed"]


async def _footprint(aconn) -> tuple[int, int, int]:
    return (
        await aconn.fetchval("SELECT count(*) FROM users"),
        await aconn.fetchval("SELECT count(*) FROM oauth_identities"),
        await aconn.fetchval("SELECT count(*) FROM sessions"),
    )


async def _only_failure(aconn) -> dict:
    rows = await aconn.fetch(
        "SELECT kind, user_id, actor_id, provider, ip, user_agent, meta::text AS meta FROM login_events"
    )
    assert len(rows) == 1
    row = dict(rows[0])
    row["meta"] = json.loads(row["meta"])
    return row


_NOT_ADMIN_FAILURE = {
    "kind": "login_failed",
    "user_id": None,
    "actor_id": None,
    "provider": "google",
    "ip": None,
    "user_agent": None,
    "meta": {"reason": "not_admin"},
}


@pytest.mark.asyncio
async def test_a_visitor_who_is_no_admin_is_refused_before_anything_is_written(auth_client, aconn, monkeypatch):
    """Sign-in is for administrators only. Anyone else is sent back to /login
    with no account, identity or session created, and the one failure record
    keeps the reason alone: no email, address or browser."""
    info = {"sub": "stranger-sub", "email": "stranger@x", "email_verified": True, "name": "S", "avatar_url": None}

    resp = await _google_callback(auth_client, monkeypatch, info, next_url="/agencies/1")

    assert resp.status_code == 302
    assert resp.headers["location"] == "http://test/login?error=not_admin&next=%2Fagencies%2F1"
    assert "sid=" not in resp.headers.get("set-cookie", "")
    assert await _footprint(aconn) == (0, 0, 0)
    assert await _only_failure(aconn) == _NOT_ADMIN_FAILURE


@pytest.mark.asyncio
async def test_a_linked_identity_whose_account_is_no_admin_is_refused(auth_client, aconn, monkeypatch):
    """A linked identity is no pass on its own: an account whose role is
    ``user`` and whose email is not in ADMIN_EMAILS gets no session."""
    uid = await aconn.fetchval("INSERT INTO users (email, role) VALUES ('member@x', 'user') RETURNING user_id")
    await aconn.execute(
        "INSERT INTO oauth_identities (provider, provider_sub, user_id, email_at_link)"
        " VALUES ('google', 'member-sub', $1, 'member@x')",
        uid,
    )
    info = {"sub": "member-sub", "email": "member@x", "email_verified": True, "name": "M", "avatar_url": None}

    resp = await _google_callback(auth_client, monkeypatch, info)

    assert resp.headers["location"] == "http://test/login?error=not_admin&next=%2F"
    assert await _footprint(aconn) == (1, 1, 0)
    assert await aconn.fetchval("SELECT role FROM users WHERE user_id=$1", uid) == "user"
    assert await _only_failure(aconn) == _NOT_ADMIN_FAILURE


@pytest.mark.asyncio
async def test_a_verified_email_does_not_link_to_an_account_that_is_no_admin(auth_client, aconn, monkeypatch):
    """Auto-linking by verified email reaches only an admin account: a
    ``user`` account with the same email is neither linked nor signed in."""
    uid = await aconn.fetchval("INSERT INTO users (email, role) VALUES ('member@x', 'user') RETURNING user_id")
    info = {"sub": "fresh-sub", "email": "Member@X", "email_verified": True, "name": "M", "avatar_url": None}

    resp = await _google_callback(auth_client, monkeypatch, info)

    assert resp.headers["location"] == "http://test/login?error=not_admin&next=%2F"
    assert await _footprint(aconn) == (1, 0, 0)
    assert await aconn.fetchval("SELECT role FROM users WHERE user_id=$1", uid) == "user"
    assert await _only_failure(aconn) == _NOT_ADMIN_FAILURE
