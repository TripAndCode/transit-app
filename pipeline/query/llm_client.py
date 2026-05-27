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
        "model": "llama-3.3-70b",
    },
    "groq": {
        "key_env": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
    },
    "ollama": {
        "key_env": "OLLAMA_API_KEY",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:7b-instruct",
    },
}


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    api_key: str | None
    base_url: str
    model: str

    @property
    def is_usable(self) -> bool:
        # Ollama doesn't require a key (any string works). For the others
        # we need a real key — operators who haven't set it shouldn't have
        # the provider in the ladder.
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


# The ``(\{.*\})`` group is intentionally greedy so it spans nested objects in
# a single attempted call. Groq's ``failed_generation`` documents one call, so
# the greedy match is correct; the ``json.loads`` guard below is the safety net
# that rejects any mis-capture — do not remove it.
_FAILED_FN_RE = re.compile(
    r"<function=([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*(\{.*\})\s*\)\s*</function>",
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

    Use :meth:`chat_completions` exactly like
    ``openai.OpenAI().chat.completions.create``. Returned message object
    is the SDK's response shape.
    """

    def __init__(self) -> None:
        self._providers = _load_providers()
        self.last_error_kind: str | None = None

    def providers(self) -> list[ProviderConfig]:
        return list(self._providers)

    def chat_completions(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        temperature: float = 0.0,
        model_override: str | None = None,
    ) -> Any | None:
        """Return the first non-erroring provider's message, or None.

        On rate-limit (429) or connection error, walks down the provider
        ladder. The returned object is the OpenAI-compatible
        ``response.choices[0].message`` — pre-extracted so callers stay
        provider-agnostic.
        """
        from openai import (
            APIConnectionError,
            APITimeoutError,
            BadRequestError,
            OpenAI,
            RateLimitError,
        )

        self.last_error_kind = None
        if not self._providers:
            self.last_error_kind = "no_providers"
            _log.error("CHAT_PROVIDERS resolves to zero usable providers")
            return None

        last_kind: str | None = None
        for cfg in self._providers:
            for attempt in (1, 2):
                try:
                    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
                    resp = client.chat.completions.create(
                        model=model_override or cfg.model,
                        messages=messages,
                        tools=tools,
                        tool_choice=tool_choice if tools else "none",
                        temperature=temperature,
                    )
                    return resp.choices[0].message
                except (APIConnectionError, APITimeoutError) as exc:
                    last_kind = "connection"
                    if attempt == 1:
                        _log.info("transient %s on %s — retrying once", exc.__class__.__name__, cfg.name)
                        continue  # retry SAME provider once
                    _log.warning("provider %s transient-failed twice; next in ladder", cfg.name)
                    break
                except RateLimitError:
                    last_kind = "rate_limit"
                    _log.warning("provider %s rate-limited (429); next in ladder", cfg.name)
                    break  # 429 won't clear in 1s — go to next provider
                except BadRequestError as exc:
                    recovered = _recover_tool_call(exc)
                    if recovered is not None:
                        _log.info("recovered tool_use_failed from %s via failed_generation", cfg.name)
                        return recovered
                    last_kind = "bad_request"
                    _log.warning("provider %s BadRequestError (unrecoverable); next in ladder", cfg.name)
                    break
                except Exception as exc:
                    last_kind = "unexpected"
                    _log.warning("provider %s unexpected %s: %r; next in ladder", cfg.name, exc.__class__.__name__, exc)
                    break

        self.last_error_kind = last_kind
        _log.error("all LLM providers exhausted; last_error_kind=%s", last_kind)
        return None


_singleton: LLMClient | None = None


def get_client() -> LLMClient:
    global _singleton
    if _singleton is None:
        _singleton = LLMClient()
    return _singleton


def reset_client_for_tests() -> None:
    global _singleton
    _singleton = None
