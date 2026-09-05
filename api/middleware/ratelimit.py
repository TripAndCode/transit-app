"""Rate limiting.

Houses two related but distinct mechanisms:

* The generic per-minute ``slowapi`` limiter (``FREE_LIMIT`` / ``PRO_LIMIT``)
  applied to most mutating/expensive routes.
* A narrower daily quota on anonymous (unauthenticated) Stage-3 LLM calls in
  the Ask flow — see ``pipeline.query.chat.chat_with_tools`` for where the
  quota is actually checked/consumed, immediately around the real LLM
  invocation so a question resolved by the deterministic router or the
  embedding-cache never touches it.

Both live here because they're the same kind of thing (server-side call-
volume limiting) even though they gate different scopes and windows, and
because this repo's convention is one canonical home per concern rather than
a second, unrelated rate-limiting mechanism bolted on elsewhere.
"""

import os
import secrets
from typing import Callable

from itsdangerous import BadSignature, URLSafeTimedSerializer
from limits import parse as _parse_limit
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from api.security import cookie_secure

FREE_LIMIT = "60/minute"
PRO_LIMIT = "600/minute"


def _key_func(request: Request) -> str:
    # get_remote_address() reads request.client.host, which uvicorn's
    # ProxyHeadersMiddleware (--forwarded-allow-ips='*', see Dockerfile) sets
    # from X-Forwarded-For. That trust is only as good as Railway's edge
    # fully replacing (not appending to) any client-supplied XFF — see the
    # CAVEAT in the Dockerfile. Unverified; this is the free-tier IP bucket
    # that assumption protects.
    if getattr(request.state, "tier", "free") == "pro":
        return f"pro:{request.headers.get('X-API-Key', 'anon')}"
    return get_remote_address(request)


limiter = Limiter(key_func=_key_func)


# Anonymous Ask LLM-call daily quota: a per-day budget on the Stage-3 LLM path
# only, on top of the per-minute FREE_LIMIT above (which is sized for generic
# abuse, not per-call LLM cost). Keyed primarily by a signed httpOnly
# anon-session cookie, with a coarser per-IP ceiling as the backstop against
# one source cycling cookies to launder around the per-session limit.
# Logged-in users are subject to neither bucket. Both buckets are process-local
# like FREE_LIMIT/PRO_LIMIT above, so a multi-instance deployment needs a
# shared storage backend instead. See docs/features/ask-tab.md.

ASK_ANON_SESSION_COOKIE_NAME = os.environ.get("ASK_ANON_SESSION_COOKIE_NAME", "ask_anon_sid")
ASK_ANON_SESSION_TTL_DAYS = int(os.environ.get("ASK_ANON_SESSION_TTL_DAYS", "30"))

ASK_ANON_QUOTA_EXCEEDED_CODE = "ask_anon_quota_exceeded"

_ANON_QUOTA_MESSAGE = {
    "ja": (
        "本日の無料AI質問の上限に達しました。"
        "ルート一覧や遅延ランキングなどの質問は引き続きご利用いただけます。時間をおいて再度お試しください。"
    ),
    "en": (
        "Today's free AI question limit has been reached. "
        "Questions like route lists or delay rankings still work without it. Please try again later."
    ),
}


def ask_anon_daily_limit() -> int:
    """Per-anon-session daily cap on Stage-3 LLM calls (``ASK_ANON_DAILY_LIMIT``).

    Default 5: enough for a genuinely curious visitor to try a handful of
    real questions before hitting the wall, small enough that a scripted
    anonymous caller can't run up a meaningful LLM bill before the daily
    reset. Read live (not import-frozen) so tests/ops can retune without a
    reimport.
    """
    return int(os.environ.get("ASK_ANON_DAILY_LIMIT", "5"))


def ask_anon_ip_daily_limit() -> int:
    """Per-IP daily backstop cap on Stage-3 LLM calls (``ASK_ANON_IP_DAILY_LIMIT``).

    Default 20 — intentionally looser than :func:`ask_anon_daily_limit`. It
    only exists to cap wholesale abuse from a single source cycling through
    many anon-session cookies, not to further restrict the common case of a
    handful of distinct legitimate visitors sharing one IP (e.g. office wifi).
    """
    return int(os.environ.get("ASK_ANON_IP_DAILY_LIMIT", "20"))


def copilot_anon_daily_limit() -> int:
    """Per-anon-session daily cap on proactive Copilot insight calls
    (``COPILOT_ANON_DAILY_LIMIT``). Looser than the Ask default since each
    call is a cheap template-selection call, not a full RAG answer.
    """
    return int(os.environ.get("COPILOT_ANON_DAILY_LIMIT", "20"))


def copilot_anon_ip_daily_limit() -> int:
    """Per-IP daily backstop for Copilot insight calls (``COPILOT_ANON_IP_DAILY_LIMIT``)."""
    return int(os.environ.get("COPILOT_ANON_IP_DAILY_LIMIT", "80"))


_SCOPE_DEFAULT_LIMITS: dict[str, tuple[Callable[[], int], Callable[[], int]]] = {
    "ask": (ask_anon_daily_limit, ask_anon_ip_daily_limit),
    "copilot": (copilot_anon_daily_limit, copilot_anon_ip_daily_limit),
}


def anon_quota_enabled() -> bool:
    """Kill switch: True unless ``ASK_ANON_QUOTA_ENABLED`` is falsy.

    Read live (not import-frozen) so ops can disable the quota without a
    redeploy if it ever misbehaves — e.g. blocks legitimate anonymous
    traffic — while the rest of the Ask flow keeps working unchanged.

    Scope: this flag only gates quota enforcement. :func:`get_or_issue_anon_session`
    still issues/reads the tracking cookie for anonymous callers regardless
    of this flag, since the cookie itself is otherwise harmless and other
    code shouldn't have to guess whether it's present.
    """
    return os.environ.get("ASK_ANON_QUOTA_ENABLED", "true").strip().lower() not in ("0", "false", "no")


_anon_quota_storage = MemoryStorage()
_anon_quota_strategy = FixedWindowRateLimiter(_anon_quota_storage)


def reset_anon_quota_for_tests() -> None:
    """Discard all counted anon-quota state.

    Call between test cases that exercise :func:`check_and_consume_anon_quota`
    so counts from one test don't leak into the next (mirrors
    ``pipeline.query.llm_client.reset_client_for_tests``'s rationale).
    """
    global _anon_quota_storage, _anon_quota_strategy
    _anon_quota_storage = MemoryStorage()
    _anon_quota_strategy = FixedWindowRateLimiter(_anon_quota_storage)


def check_and_consume_anon_quota(
    session_key: str,
    ip_key: str,
    *,
    scope: str = "ask",
    daily_limit: int | None = None,
    ip_daily_limit: int | None = None,
) -> bool:
    """Atomically test-then-consume one unit from both the per-session and
    per-IP daily anon LLM-call quotas.

    Returns True — having consumed one unit from BOTH buckets — only when
    both currently have room. Returns False — consuming from NEITHER — the
    moment either is exhausted, so a call that's ultimately declined never
    burns down a quota bucket it was refused against.

    Safe under this process's asyncio concurrency without a lock: there is
    no ``await`` between the ``test()`` and ``hit()`` calls below, and
    nothing else can run in between on a single-threaded event loop.

    Always returns True when :func:`anon_quota_enabled` is False.

    ``scope`` namespaces the counted buckets (e.g. ``"ask"`` vs.
    ``"copilot"``) so independent callers never share a counter, and also
    picks the scope-appropriate default limits via :data:`_SCOPE_DEFAULT_LIMITS`
    when ``daily_limit``/``ip_daily_limit`` aren't given explicitly. The
    default ``scope="ask"`` reproduces today's `/ask` behavior exactly, since
    both existing callers invoke this positionally with no keyword args.
    """
    if not anon_quota_enabled():
        return True
    default_limit, default_ip_limit = _SCOPE_DEFAULT_LIMITS[scope]
    limit = daily_limit if daily_limit is not None else default_limit()
    ip_limit = ip_daily_limit if ip_daily_limit is not None else default_ip_limit()
    session_item = _parse_limit(f"{limit}/day")
    ip_item = _parse_limit(f"{ip_limit}/day")
    session_id = f"{scope}:sess:{session_key}"
    ip_id = f"{scope}:ip:{ip_key}"
    if not _anon_quota_strategy.test(session_item, session_id):
        return False
    if not _anon_quota_strategy.test(ip_item, ip_id):
        return False
    _anon_quota_strategy.hit(session_item, session_id)
    _anon_quota_strategy.hit(ip_item, ip_id)
    return True


class AnonQuotaContext:
    """The two keys :func:`check_and_consume_anon_quota` needs for one caller.

    Built by ``api/routers/ask.py`` for unauthenticated callers only, and
    threaded through to ``pipeline.query.chat.chat_with_tools`` so the quota
    check can happen immediately around the actual LLM invocation rather
    than at the endpoint level.
    """

    __slots__ = ("ip_key", "session_key")

    def __init__(self, session_key: str, ip_key: str) -> None:
        self.session_key = session_key
        self.ip_key = ip_key


_anon_session_signer = URLSafeTimedSerializer(
    os.environ.get("SESSION_SIGNING_KEY", "dev-only-not-secret"),
    salt="ask-anon-quota",
)


def anon_ip_key(request: Request) -> str:
    """IP identifier for the anon quota's per-IP backstop bucket.

    Always the plain remote address — the anon quota only ever applies to
    unauthenticated callers, so there's no pro-tier branching to mirror from
    ``_key_func`` above.
    """
    return get_remote_address(request)


def get_or_issue_anon_session(request: Request, response: Response) -> str:
    """Return the caller's anon-session id, verifying the signed cookie if
    present, or mint + set a fresh signed httpOnly cookie on ``response`` if
    missing, tampered with, or expired.

    Only meaningful for unauthenticated callers — logged-in users are never
    subject to the anon quota and callers should not invoke this for them.
    """
    raw = request.cookies.get(ASK_ANON_SESSION_COOKIE_NAME)
    if raw:
        try:
            sid = _anon_session_signer.loads(raw, max_age=ASK_ANON_SESSION_TTL_DAYS * 86400)
        except BadSignature:
            sid = None
        if isinstance(sid, str) and sid:
            return sid

    sid = secrets.token_urlsafe(24)
    response.set_cookie(
        ASK_ANON_SESSION_COOKIE_NAME,
        _anon_session_signer.dumps(sid),
        max_age=ASK_ANON_SESSION_TTL_DAYS * 86400,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )
    return sid


class AnonAskQuotaExceeded(Exception):
    """Raised by ``pipeline.query.chat.chat_with_tools`` when an anonymous
    caller's daily Stage-3 LLM quota is exhausted.

    Propagates uncaught through ``api/routers/ask.py`` (mirrors how an
    ``asyncpg.exceptions.UndefinedTableError`` from the same endpoint
    propagates to ``aggregate_not_ready_handler``) to
    :func:`ask_quota_exceeded_handler` (registered in ``api.main``), which
    turns it into a 429 carrying a machine-readable ``code`` distinct from
    both slowapi's generic ``RateLimitExceeded`` response and a plain 500,
    so a caller can detect and react to it specifically rather than
    treating it as a generic error.
    """


async def ask_quota_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map :class:`AnonAskQuotaExceeded` to a localized 429."""
    from api.deps import get_locale

    locale = get_locale(request)
    detail = _ANON_QUOTA_MESSAGE.get(locale, _ANON_QUOTA_MESSAGE["ja"])
    return JSONResponse(status_code=429, content={"detail": detail, "code": ASK_ANON_QUOTA_EXCEEDED_CODE})


COPILOT_ANON_QUOTA_EXCEEDED_CODE = "copilot_anon_quota_exceeded"

_COPILOT_ANON_QUOTA_MESSAGE = {
    "ja": "本日の無料AIコパイロットの上限に達しました。時間をおいて再度お試しください。",
    "en": "Today's free AI Copilot limit has been reached. Please try again later.",
}


class AnonCopilotQuotaExceeded(Exception):
    """Raised when an anonymous caller's daily Copilot-insight quota is exhausted."""


async def copilot_quota_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map :class:`AnonCopilotQuotaExceeded` to a localized 429 (mirrors ``ask_quota_exceeded_handler`` exactly)."""
    from api.deps import get_locale

    locale = get_locale(request)
    detail = _COPILOT_ANON_QUOTA_MESSAGE.get(locale, _COPILOT_ANON_QUOTA_MESSAGE["ja"])
    return JSONResponse(status_code=429, content={"detail": detail, "code": COPILOT_ANON_QUOTA_EXCEEDED_CODE})
