"""One-off validation call for a user-supplied BYOK provider key.

Meant to run before the key is ever persisted, so an invalid key is rejected
immediately instead of silently stored and discovered broken only when the
Copilot later fails. No caller wires this in yet.
"""

from __future__ import annotations

import openai

from pipeline.query.llm_client import _PROVIDER_DEFAULTS
from pipeline.query.user_llm_keys import ALLOWED_PROVIDERS


async def validate_provider_key(provider: str, api_key: str) -> bool:
    if provider not in ALLOWED_PROVIDERS:
        return False
    defaults = _PROVIDER_DEFAULTS[provider]  # plain dict: {"key_env", "base_url", "model"}
    client = openai.AsyncOpenAI(
        api_key=api_key, base_url=defaults["base_url"], max_retries=0, timeout=10.0
    )
    try:
        await client.chat.completions.create(
            model=defaults["model"],
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        return True
    except (openai.AuthenticationError, openai.PermissionDeniedError):
        return False
    except (openai.APIConnectionError, openai.APITimeoutError):
        # A network/connection failure proves nothing about the key
        # itself — surface it instead of misreporting the key as valid.
        raise
    except openai.APIError:
        # A non-auth provider error (rate limit, model unavailable) does
        # not prove the key itself is invalid — treat as valid rather
        # than blocking a legitimate key on a transient provider hiccup.
        return True
    finally:
        await client.close()
