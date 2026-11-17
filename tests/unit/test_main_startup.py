"""Tests for module-level startup validators in ``api.main``."""

import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import api.main
from api.main import _DEV_SIGNING_KEY, _validate_cors_origins, _validate_session_signing_key
from api.middleware.forwarded import ForwardedClientMiddleware


def test_validate_cors_origins_rejects_wildcard_with_credentials():
    """`Access-Control-Allow-Origin: *` is incompatible with
    `Access-Control-Allow-Credentials: true` per the CORS spec —
    browsers silently block the response. Fail loud at startup instead."""
    with pytest.raises(RuntimeError, match="forbids"):
        _validate_cors_origins(["*"], allow_credentials=True)


def test_validate_cors_origins_allows_wildcard_without_credentials():
    """`*` is fine when credentials are off."""
    _validate_cors_origins(["*"], allow_credentials=False)


def test_validate_cors_origins_allows_explicit_with_credentials():
    """An explicit allowlist + credentials is the supported combo."""
    _validate_cors_origins(["http://localhost:5173"], allow_credentials=True)


def test_validate_cors_origins_allows_explicit_without_credentials():
    """No-op path — explicit allowlist, no credentials."""
    _validate_cors_origins(["http://localhost:5173"], allow_credentials=False)


def test_validate_cors_origins_rejects_mixed_list_with_wildcard_and_credentials():
    """A list containing `*` alongside explicit origins is still
    spec-incompatible when credentials are enabled. Guards against a
    future refactor that naively checks `origins == ["*"]` instead of
    `"*" in origins`."""
    with pytest.raises(RuntimeError, match="forbids"):
        _validate_cors_origins(["http://localhost:5173", "*"], allow_credentials=True)


_SIGN_IN_ENV = (*api.main._AUTH_ENV, "SESSION_SIGNING_KEY", "DEFAULT_ADMIN_USERNAME", "DEFAULT_ADMIN_PASSWORD")


@pytest.fixture
def no_sign_in_env(monkeypatch):
    """Startup reads the OAuth, signing-key and local-admin variables live, so a
    shell exporting any of them would change which boot these tests exercise."""
    for var in _SIGN_IN_ENV:
        monkeypatch.delenv(var, raising=False)


def test_session_key_guard_rejects_dev_default_when_auth_enabled():
    """Booting an auth-enabled deployment with the dev signing key would make
    every session/OAuth cookie forgeable. Fail loud at startup instead."""
    with pytest.raises(RuntimeError, match="SESSION_SIGNING_KEY"):
        _validate_session_signing_key(enabled=True, signing_key=_DEV_SIGNING_KEY)


def test_session_key_guard_allows_real_key_when_auth_enabled():
    """A real secret is the supported configuration — no error."""
    _validate_session_signing_key(enabled=True, signing_key="a-real-random-secret")


def test_session_key_guard_rejects_a_missing_key_when_auth_enabled():
    with pytest.raises(RuntimeError, match="openssl rand -hex 32"):
        _validate_session_signing_key(enabled=True, signing_key=None)


def test_session_key_guard_ignores_dev_default_when_auth_disabled():
    """Anonymous-only mode never mints those cookies, so the dev default is
    harmless there — don't block local/anon boots."""
    _validate_session_signing_key(enabled=False, signing_key=_DEV_SIGNING_KEY)


def test_openapi_docs_off_when_unset(monkeypatch):
    """The closed default. `PUBLIC_BASE_URL` is deliberately not consulted: it
    is only set when SSO is configured, so a live HTTPS deployment without SSO
    leaves it at its localhost default and would read as local dev."""
    monkeypatch.delenv("OPENAPI_DOCS_ENABLED", raising=False)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://transit.example.com")
    assert api.main._openapi_docs_enabled() is False

    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")
    assert api.main._openapi_docs_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " true "])
def test_openapi_docs_on_for_truthy_flag(monkeypatch, value):
    monkeypatch.setenv("OPENAPI_DOCS_ENABLED", value)
    assert api.main._openapi_docs_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "", "maybe"])
def test_openapi_docs_off_for_anything_else(monkeypatch, value):
    monkeypatch.setenv("OPENAPI_DOCS_ENABLED", value)
    assert api.main._openapi_docs_enabled() is False


def test_env_example_enables_docs_for_local_dev():
    """Local dev keeps its docs: the template every developer copies opts in,
    while a deployment setting its variables directly inherits the closed
    default."""
    env_example = (pathlib.Path(__file__).resolve().parents[2] / ".env.example").read_text()
    assert "\nOPENAPI_DOCS_ENABLED=true" in env_example


def test_app_disables_the_builtin_docs_urls():
    """The built-ins are always off -- the app registers its own /docs,
    /redoc and /openapi.json routes instead, each gated per-request by
    `_require_docs_enabled` (see the tests below), since FastAPI's own
    `docs_url`/`redoc_url`/`openapi_url` are fixed for the process's whole
    lifetime once passed to the constructor and can't reflect a `flags`
    override without a restart."""
    assert (api.main.app.docs_url, api.main.app.redoc_url, api.main.app.openapi_url) == (None, None, None)


def test_require_docs_enabled_blocks_when_flag_off(monkeypatch):
    monkeypatch.setenv("OPENAPI_DOCS_ENABLED", "false")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(Exception) as exc_info:
        api.main._require_docs_enabled()
    assert exc_info.value.status_code == 404


def test_require_docs_enabled_allows_when_flag_on(monkeypatch):
    monkeypatch.setenv("OPENAPI_DOCS_ENABLED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    api.main._require_docs_enabled()  # must not raise


def test_dockerfile_reads_the_client_address_from_its_one_edge_proxy():
    """The production image reads the client address from the X-Forwarded-For
    entry Railway's edge appended, and keeps uvicorn's own handling off: told
    to trust every peer, that reads the leftmost entry, which a client writes."""
    dockerfile = (pathlib.Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
    assert "ENV FORWARDED_HOPS=1" in dockerfile
    assert "--no-proxy-headers" in dockerfile
    assert "--forwarded-allow-ips" not in dockerfile


async def test_lifespan_closes_pool_when_post_pool_setup_fails(no_sign_in_env, monkeypatch):
    """``lifespan``'s cleanup after ``yield`` only runs once ``yield`` is
    reached -- a failure in post-pool startup (here, seed_local_admin) raises
    before that point and must close what startup already opened itself
    instead of leaking it. That includes the ClickHouse client: it is opened
    inside the same guarded block, so closing only the pool leaks its HTTP
    session on every failed boot."""

    mock_pool = AsyncMock()
    mock_ch_client = AsyncMock()

    monkeypatch.setattr(api.main.asyncpg, "create_pool", AsyncMock(return_value=mock_pool))
    monkeypatch.setattr(api.main, "get_ch_client", AsyncMock(return_value=mock_ch_client))
    monkeypatch.setattr(api.main, "seed_local_admin", AsyncMock(side_effect=RuntimeError("boom")))

    fake_app = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(RuntimeError, match="boom"):
        async with api.main.lifespan(fake_app):
            pass

    mock_pool.close.assert_awaited_once()
    mock_ch_client.close.assert_awaited_once()


async def test_lifespan_failure_reports_the_startup_error_not_a_cleanup_error(no_sign_in_env, monkeypatch):
    """A failing close() must not replace the error that caused the shutdown.

    The startup exception is the actionable one; surfacing a secondary
    close() failure in its place would send an operator after the wrong
    thing."""

    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock(side_effect=RuntimeError("close exploded"))
    mock_ch_client = AsyncMock()
    mock_ch_client.close = AsyncMock(side_effect=RuntimeError("ch close exploded"))

    monkeypatch.setattr(api.main.asyncpg, "create_pool", AsyncMock(return_value=mock_pool))
    monkeypatch.setattr(api.main, "get_ch_client", AsyncMock(return_value=mock_ch_client))
    monkeypatch.setattr(api.main, "seed_local_admin", AsyncMock(side_effect=RuntimeError("boom")))

    fake_app = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(RuntimeError, match="boom"):
        async with api.main.lifespan(fake_app):
            pass


async def test_lifespan_opens_the_pool_with_its_session_settings_as_startup_parameters(no_sign_in_env, monkeypatch):
    """A SET in a connection init is undone by the RESET ALL the pool runs on
    every release; a startup parameter is what RESET ALL returns to. A failing
    seed_local_admin ends startup right after the pool opens."""
    create_pool = AsyncMock(return_value=AsyncMock())
    monkeypatch.setattr(api.main.asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(api.main, "get_ch_client", AsyncMock(return_value=AsyncMock()))
    monkeypatch.setattr(api.main, "seed_local_admin", AsyncMock(side_effect=RuntimeError("stop")))

    with pytest.raises(RuntimeError, match="stop"):
        async with api.main.lifespan(SimpleNamespace(state=SimpleNamespace())):
            pass

    kwargs = create_pool.await_args.kwargs
    assert kwargs["server_settings"] == api.main.PG_SESSION_SETTINGS
    assert "init" not in kwargs


def test_the_forwarded_client_address_is_set_outside_every_other_middleware():
    """The access log, the rate limiter and every audit row read
    request.client, so the forwarded address must be in place before any of
    them runs. add_middleware puts the latest addition first."""
    assert api.main.app.user_middleware[0].cls is ForwardedClientMiddleware


async def test_lifespan_boots_with_no_llm_provider_configured(no_sign_in_env, monkeypatch, caplog):
    """A deployment with no LLM key still serves the deterministic notebook, so
    an empty provider ladder must not stop startup. seed_local_admin failing
    marks the first step after the provider check was passed."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CHAT_PROVIDERS", raising=False)
    monkeypatch.setattr(api.main.asyncpg, "create_pool", AsyncMock(return_value=AsyncMock()))
    monkeypatch.setattr(api.main, "get_ch_client", AsyncMock(return_value=AsyncMock()))
    monkeypatch.setattr(api.main, "seed_local_admin", AsyncMock(side_effect=RuntimeError("reached")))
    from pipeline.query import llm_client

    llm_client.reset_client_for_tests()
    try:
        with caplog.at_level("WARNING", logger=api.main._log.name):
            with pytest.raises(RuntimeError, match="reached"):
                async with api.main.lifespan(SimpleNamespace(state=SimpleNamespace())):
                    pass
    finally:
        llm_client.reset_client_for_tests()
    assert "No LLM provider key configured" in caplog.text


_OAUTH = {
    "GOOGLE_CLIENT_ID": "g",
    "GOOGLE_CLIENT_SECRET": "gs",
    "GITHUB_CLIENT_ID": "h",
    "GITHUB_CLIENT_SECRET": "hs",
}
_LOCAL_ADMIN = {"DEFAULT_ADMIN_USERNAME": "root@local", "DEFAULT_ADMIN_PASSWORD": "correct-horse-battery-staple"}
_REAL_KEY = {"SESSION_SIGNING_KEY": "a-real-random-secret"}
_DEV_KEY = {"SESSION_SIGNING_KEY": _DEV_SIGNING_KEY}
_KEY_MESSAGE = "SESSION_SIGNING_KEY is missing or the public dev default"
_PARTIAL_MESSAGE = "Partial OAuth env"


def _first_n_oauth_vars(n: int) -> dict[str, str]:
    return dict(list(_OAUTH.items())[:n])


@pytest.mark.parametrize(
    ("env", "refusal"),
    [
        pytest.param({}, None, id="anonymous-no-key-boots"),
        pytest.param({"SESSION_SIGNING_KEY": ""}, None, id="anonymous-blank-key-boots"),
        pytest.param(_REAL_KEY, None, id="key-alone-boots"),
        pytest.param(_LOCAL_ADMIN, _KEY_MESSAGE, id="local-admin-no-key-refused"),
        pytest.param({**_LOCAL_ADMIN, "SESSION_SIGNING_KEY": ""}, _KEY_MESSAGE, id="local-admin-blank-key-refused"),
        pytest.param({**_LOCAL_ADMIN, **_DEV_KEY}, _KEY_MESSAGE, id="local-admin-dev-key-refused"),
        pytest.param({**_LOCAL_ADMIN, **_REAL_KEY}, None, id="local-admin-real-key-boots"),
        pytest.param({**_OAUTH, **_REAL_KEY}, None, id="oauth-real-key-boots"),
        pytest.param(_OAUTH, _KEY_MESSAGE, id="oauth-no-key-refused"),
        pytest.param({**_OAUTH, **_DEV_KEY}, _KEY_MESSAGE, id="oauth-dev-key-refused"),
        pytest.param({**_OAUTH, **_LOCAL_ADMIN, **_REAL_KEY}, None, id="oauth-and-local-admin-real-key-boots"),
        *(
            pytest.param(
                {**_first_n_oauth_vars(n), **_REAL_KEY}, _PARTIAL_MESSAGE, id=f"partial-oauth-{n}-of-4-refused"
            )
            for n in (1, 2, 3)
        ),
        pytest.param(
            {"GITHUB_CLIENT_SECRET": "hs", **_LOCAL_ADMIN, **_REAL_KEY},
            _PARTIAL_MESSAGE,
            id="partial-oauth-with-local-admin-refused",
        ),
    ],
)
async def test_lifespan_sign_in_configuration(no_sign_in_env, monkeypatch, env, refusal):
    """Every sign-in method needs a real signing key; OAuth needs all four of its
    client variables or none. seed_local_admin runs right after both checks, so
    reaching it is what "boots" means here."""
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    monkeypatch.setattr(api.main.asyncpg, "create_pool", AsyncMock(return_value=AsyncMock()))
    monkeypatch.setattr(api.main, "get_ch_client", AsyncMock(return_value=AsyncMock()))
    monkeypatch.setattr(api.main, "seed_local_admin", AsyncMock(side_effect=RuntimeError("booted")))

    with pytest.raises(RuntimeError, match=refusal or "booted"):
        async with api.main.lifespan(SimpleNamespace(state=SimpleNamespace())):
            pass
