"""Provider-agnostic LLM adapter with ordered fallback.

Lets the Ask tab try Cerebras first (1M tokens/day free), fall back to
Groq (100K/day) on rate-limit, and optionally bounce to a local Ollama
instance for offline safety. All three speak OpenAI-compatible REST, so
the openai-python SDK works with each by swapping ``base_url`` and
``api_key``. Falling back lets a single .env file work locally and
remotely without code-path divergence.

Configuration is fully env-driven; see ``.env.example`` for the keys.
``chat_completions`` is intentionally a thin wrapper around
``openai.OpenAI(...).chat.completions.create(...)`` so calling code in
``pipeline/query/chat.py`` doesn't have to know which provider answered.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

_log = logging.getLogger(__name__)

# Built-in defaults for the three providers we support. Operator overrides
# any field via env (e.g. CEREBRAS_BASE_URL=...). The "key_env" is the
# env-var name that holds the provider's API key.
_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "cerebras": {
        "key_env": "CEREBRAS_API_KEY",
        "base_url": "https://api.cerebras.ai/v1",
        "model": "gpt-oss-120b",
    },
    "groq": {
        "key_env": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        # llama-3.3-70b-versatile was decommissioned by Groq; gpt-oss-120b
        # matches the Cerebras default so the two ladder rungs behave
        # consistently. Override via GROQ_MODEL if your account's available
        # models differ (check `GET /openai/v1/models`).
        "model": "openai/gpt-oss-120b",
    },
    "ollama": {
        "key_env": "OLLAMA_API_KEY",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:7b-instruct",
    },
}


@dataclass(frozen=True)
class ProviderConfig:
    """Immutable configuration for one LLM provider entry in the fallback ladder.

    Loaded once at startup from env-vars by :func:`_load_providers`.
    ``is_usable`` gates whether the provider enters the ladder at all —
    Ollama requires no API key; all others need one.
    """

    name: str
    api_key: str | None
    base_url: str
    model: str

    @property
    def is_usable(self) -> bool:
        """Return True when this provider has enough config to attempt a call."""
        return bool(self.api_key) or self.name == "ollama"


def _load_provider(name: str) -> ProviderConfig | None:
    """Resolve env-vars for one provider; return None if missing entirely."""
    defaults = _PROVIDER_DEFAULTS.get(name)
    if defaults is None:
        _log.warning("CHAT_PROVIDERS lists unknown provider %r — skipping", name)
        return None
    upper = name.upper()
    key = os.environ.get(defaults["key_env"]) or ("ollama" if name == "ollama" else None)
    base = os.environ.get(f"{upper}_BASE_URL", defaults["base_url"])
    model = os.environ.get(f"{upper}_MODEL", defaults["model"])
    cfg = ProviderConfig(name=name, api_key=key, base_url=base, model=model)
    if not cfg.is_usable:
        _log.info("provider %s skipped — no api key configured", name)
        return None
    return cfg


def _load_providers() -> list[ProviderConfig]:
    """Read CHAT_PROVIDERS and return usable, ordered configs."""
    raw = os.environ.get("CHAT_PROVIDERS", "groq")  # back-compat default
    names = [n.strip().lower() for n in raw.split(",") if n.strip()]
    cfgs = [c for c in (_load_provider(n) for n in names) if c is not None]
    if not cfgs:
        _log.error("CHAT_PROVIDERS=%r resolves to zero usable providers", raw)
    return cfgs


# Groq/llama leak a failed tool call in a few shapes: ``name({...})</function>``,
# ``name>{...}</function>``, or bare ``name{...}``. Capture the function name,
# skip any chars up to the first ``{``, then greedily grab to the last ``}`` so a
# single call's nested objects are spanned. The ``json.loads`` guard below is the
# safety net that rejects any mis-capture — do not remove it.
_FAILED_FN_RE = re.compile(
    r"<function=([A-Za-z_][A-Za-z0-9_]*)[^{}]*(\{.*\})",
    re.DOTALL,
)


def _recover_tool_call(exc: Exception) -> SimpleNamespace | None:
    """Salvage a malformed tool call from a Groq ``tool_use_failed`` 400.

    Groq returns the model's attempted call in ``error.failed_generation``,
    e.g. ``<function=top_n({"metric":"avg_delay","n":10})</function>``. Parse
    the name + JSON args into a message object whose ``.tool_calls`` match the
    OpenAI shape ``chat.py`` already consumes (``call.function.name`` /
    ``call.function.arguments`` as a JSON string). Returns ``None`` if the
    error is not a tool_use_failed or the payload can't be parsed — callers
    must then fall through (never dispatch unparsed args).
    """
    body = getattr(exc, "body", None) or {}
    err = body.get("error", {}) if isinstance(body, dict) else {}
    if err.get("code") != "tool_use_failed":
        return None
    raw = err.get("failed_generation")
    if not raw:
        return None
    m = _FAILED_FN_RE.search(raw)
    if not m:
        return None
    name, args_json = m.group(1), m.group(2)
    try:
        json.loads(args_json)  # validate; keep original string form for downstream json.loads
    except (json.JSONDecodeError, TypeError):
        return None
    call = SimpleNamespace(
        id=f"recovered_{uuid.uuid4().hex[:8]}",
        type="function",
        function=SimpleNamespace(name=name, arguments=args_json),
    )
    return SimpleNamespace(content=None, tool_calls=[call])


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
        (``APIConnectionError``, no backoff — it fails instantly), descends
        the ladder immediately on a timeout (``APITimeoutError``, which already
        waited the full deadline) and on rate-limit (429), and on a Groq
        ``tool_use_failed`` 400 salvages the call via ``_recover_tool_call``
        instead of failing over.

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
                    create_kwargs: dict[str, Any] = dict(
                        model=model_override or cfg.model,
                        messages=messages,
                        tools=tools,
                        tool_choice=tool_choice if tools else "none",
                        temperature=temperature,
                    )
                    if response_format is not None:
                        create_kwargs["response_format"] = response_format
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
                    recovered = _recover_tool_call(exc)
                    if recovered is not None:
                        _log.info("recovered tool_use_failed from %s via failed_generation", cfg.name)
                        return recovered, None
                    last_kind = "bad_request"
                    _log.warning("provider %s BadRequestError (unrecoverable); next in ladder", cfg.name)
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
