"""Provider-agnostic LLM adapter with ordered fallback.

Lets the Ask tab try Gemini first (free request-count quota), then fall
back to OpenAI as a paid last resort once the free tier is exhausted.
Both speak OpenAI-compatible REST, so the openai-python SDK works with
each by swapping ``base_url`` and ``api_key``. Falling back lets a
single .env file work locally and remotely without code-path
divergence.

Configuration is fully env-driven; see ``.env.example`` for the keys.
``chat_completions`` is intentionally a thin wrapper around
``openai.OpenAI(...).chat.completions.create(...)`` so calling code in
``pipeline/query/chat.py`` doesn't have to know which provider answered.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger(__name__)

# Built-in defaults for each provider we support. Operator overrides
# any field via env (e.g. GEMINI_BASE_URL=...). The "key_env" is the
# env-var name that holds the provider's API key.
_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "gemini": {
        "key_env": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        # Flash-Lite carries the most generous free daily request quota of
        # Google's OpenAI-compatible tier. Override via GEMINI_MODEL for the
        # full Flash model if quality matters more than quota headroom.
        "model": "gemini-3.1-flash-lite",
    },
    "openai": {
        "key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        # Paid rung, deliberately placed last in CHAT_PROVIDERS so the free
        # Gemini tier is exhausted first. gpt-5.4-mini is the current
        # cost-efficient mini-tier model (confirmed available via
        # GET /v1/models); override via OPENAI_MODEL if needed.
        "model": "gpt-5.4-mini",
    },
}


@dataclass(frozen=True)
class ProviderConfig:
    """Immutable configuration for one LLM provider entry in the fallback ladder.

    Loaded once at startup from env-vars by :func:`_load_providers`.
    ``is_usable`` gates whether the provider enters the ladder at all —
    every provider needs an API key.
    """

    name: str
    api_key: str | None
    base_url: str
    model: str

    @property
    def is_usable(self) -> bool:
        """Return True when this provider has enough config to attempt a call."""
        return bool(self.api_key)


def _load_provider(name: str) -> ProviderConfig | None:
    """Resolve env-vars for one provider; return None if missing entirely."""
    defaults = _PROVIDER_DEFAULTS.get(name)
    if defaults is None:
        _log.warning("CHAT_PROVIDERS lists unknown provider %r — skipping", name)
        return None
    upper = name.upper()
    key = os.environ.get(defaults["key_env"])
    base = os.environ.get(f"{upper}_BASE_URL", defaults["base_url"])
    model = os.environ.get(f"{upper}_MODEL", defaults["model"])
    cfg = ProviderConfig(name=name, api_key=key, base_url=base, model=model)
    if not cfg.is_usable:
        _log.info("provider %s skipped — no api key configured", name)
        return None
    return cfg


def _load_providers() -> list[ProviderConfig]:
    """Read CHAT_PROVIDERS and return usable, ordered configs."""
    raw = os.environ.get("CHAT_PROVIDERS", "gemini")  # back-compat default
    names = [n.strip().lower() for n in raw.split(",") if n.strip()]
    cfgs = [c for c in (_load_provider(n) for n in names) if c is not None]
    if not cfgs:
        _log.error("CHAT_PROVIDERS=%r resolves to zero usable providers", raw)
    return cfgs


def _build_create_kwargs(
    *,
    model: str,
    messages: list[dict],
    temperature: float,
    tools: list[dict] | None,
    tool_choice: str,
    response_format: dict | None,
) -> dict[str, Any]:
    """Build ``chat.completions.create`` kwargs, shared by the ladder and the
    BYOK one-off path (:func:`pipeline.query.chat._completion_with_key`) so a
    request-shape fix only ever needs to happen once.

    ``tools``/``tool_choice`` are omitted together when there are no tools,
    rather than sent as ``tools=None``/``tool_choice="none"``: OpenAI rejects
    ``tool_choice`` outright when ``tools`` is absent ("tool_choice is only
    allowed when tools are specified"). Gemini tolerates the ``None``/
    ``"none"`` pair, but omitting both keys is the one shape every
    OpenAI-compatible provider in the ladder accepts.
    """
    create_kwargs: dict[str, Any] = dict(model=model, messages=messages, temperature=temperature)
    if tools:
        create_kwargs["tools"] = tools
        create_kwargs["tool_choice"] = tool_choice
    if response_format is not None:
        create_kwargs["response_format"] = response_format
    return create_kwargs


class LLMClient:
    """Tries each configured provider in order until one succeeds.

    :meth:`chat_completions` wraps ``openai.OpenAI().chat.completions.create``
    and returns ``(message, error_kind)`` — the SDK message shape on success
    (``error_kind`` None), or ``(None, kind)`` when the ladder is exhausted.
    """

    def __init__(self) -> None:
        """Load providers from env at construction time."""
        self._providers = _load_providers()

    def providers(self) -> list[ProviderConfig]:
        """Return a snapshot of the configured provider ladder (ordered)."""
        return list(self._providers)

    def chat_completions(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        temperature: float = 0.0,
        model_override: str | None = None,
        response_format: dict | None = None,
        allowed_providers: set[str] | None = None,
    ) -> tuple[Any | None, str | None]:
        """Attempt each provider in order and return ``(message, error_kind)``.

        ``message`` is the OpenAI ``response.choices[0].message`` on success,
        or ``None`` when the ladder is fully exhausted.  ``error_kind`` is
        ``None`` on success and one of ``"rate_limit"``, ``"connection"``,
        ``"bad_request"``, ``"unexpected"``, or ``"no_providers"`` on failure,
        letting the caller select an honest user-facing degradation message.

        ``allowed_providers``, when given, restricts the ladder to providers
        whose name is in the set — used by callers that must avoid a provider
        with a known weakness (e.g. the follow-up path excludes injection-prone
        models). An empty intersection returns ``"no_providers"`` so the caller
        degrades gracefully rather than silently using a disallowed provider.

        Per provider: retries ONCE on a refused/reset socket
        (``APIConnectionError``, no backoff — it fails instantly), and
        descends the ladder immediately on a timeout (``APITimeoutError``,
        which already waited the full deadline), on rate-limit (429), and on
        an unrecoverable ``BadRequestError``.

        The client is built once per provider (reusing one connection pool
        across the retry) with ``max_retries=0`` — the SDK's own retry would
        otherwise block ~60s on a 429 before our ladder descent could fire,
        making the fallback design illusory.
        """
        from openai import (
            APIConnectionError,
            APITimeoutError,
            BadRequestError,
            OpenAI,
            RateLimitError,
        )

        ladder = self._providers
        if allowed_providers is not None:
            ladder = [c for c in ladder if c.name in allowed_providers]
        if not ladder:
            _log.error("no usable providers (allowed=%s)", allowed_providers)
            return None, "no_providers"

        seen_rate_limit = False
        last_kind: str | None = None
        for cfg in ladder:
            client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, max_retries=0)
            for attempt in (1, 2):
                try:
                    create_kwargs = _build_create_kwargs(
                        model=model_override or cfg.model,
                        messages=messages,
                        temperature=temperature,
                        tools=tools,
                        tool_choice=tool_choice,
                        response_format=response_format,
                    )
                    resp = client.chat.completions.create(**create_kwargs)
                    return resp.choices[0].message, None
                except APITimeoutError:
                    # The request already waited the full timeout; retrying would
                    # just wait it again and double tail latency. Descend instead.
                    # (Caught before APIConnectionError — APITimeoutError subclasses it.)
                    last_kind = "connection"
                    _log.warning("provider %s timed out; next in ladder", cfg.name)
                    break
                except APIConnectionError as exc:
                    # A refused/reset socket fails instantly, so one cheap retry
                    # on the same provider is worthwhile before descending.
                    last_kind = "connection"
                    if attempt == 1:
                        _log.info("transient %s on %s — retrying once", exc.__class__.__name__, cfg.name)
                        continue
                    _log.warning("provider %s connection-failed twice; next in ladder", cfg.name)
                    break
                except RateLimitError:
                    seen_rate_limit = True
                    last_kind = "rate_limit"
                    _log.warning("provider %s rate-limited (429); next in ladder", cfg.name)
                    break  # 429 won't clear in 1s — go to next provider
                except BadRequestError as exc:
                    last_kind = "bad_request"
                    _log.warning("provider %s BadRequestError %r; next in ladder", cfg.name, exc)
                    break
                except Exception as exc:
                    last_kind = "unexpected"
                    _log.warning("provider %s unexpected %s: %r; next in ladder", cfg.name, exc.__class__.__name__, exc)
                    break

        # Prefer the quota signal: if any provider was rate-limited, surface that
        # even when a later provider failed differently — its steer-to-free-
        # questions message is the most actionable thing we can show the user.
        final_kind = "rate_limit" if seen_rate_limit else last_kind
        _log.error("all LLM providers exhausted; error_kind=%s", final_kind)
        return None, final_kind


_singleton: LLMClient | None = None


def get_client() -> LLMClient:
    """Return the process-wide :class:`LLMClient` singleton.

    Constructed lazily on first call so env-vars set after import are
    picked up.  Use :func:`reset_client_for_tests` between test cases to
    prevent provider-ladder state from leaking across tests.
    """
    global _singleton
    if _singleton is None:
        _singleton = LLMClient()
    return _singleton


def reset_client_for_tests() -> None:
    """Discard the singleton so the next :func:`get_client` call re-reads env.

    Call this in test fixtures (``autouse=True``) that manipulate
    ``CHAT_PROVIDERS`` or provider API-key env-vars.
    """
    global _singleton
    _singleton = None
