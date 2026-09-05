"""Per-user BYOK LLM credential storage, encrypted at rest.

No encryption-at-rest pattern existed elsewhere in this codebase (checked
during planning), so this introduces one: Fernet (symmetric, authenticated)
from the ``cryptography`` package, keyed by ``LLM_KEY_ENCRYPTION_KEY`` (a
urlsafe-base64 32-byte key, e.g. ``Fernet.generate_key()`` output). Losing
this env var makes every stored key permanently unrecoverable — back it up
like any other production secret.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import NamedTuple

from cryptography.fernet import Fernet

ALLOWED_PROVIDERS = ("groq", "openai", "cerebras")


class UserLLMKey(NamedTuple):
    provider: str
    raw_key: str
    key_suffix: str


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = os.environ["LLM_KEY_ENCRYPTION_KEY"]
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_key(raw: str) -> bytes:
    return _fernet().encrypt(raw.encode())


def decrypt_key(blob: bytes) -> str:
    return _fernet().decrypt(bytes(blob)).decode()


def key_suffix(raw: str) -> str:
    return raw[-4:] if len(raw) >= 4 else raw


async def save_user_llm_key(conn, user_id: int, provider: str, raw_key: str) -> None:
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider!r}")
    await conn.execute(
        """
        INSERT INTO user_llm_keys (user_id, provider, encrypted_key, key_suffix, updated_at)
        VALUES ($1, $2, $3, $4, now())
        ON CONFLICT (user_id) DO UPDATE
        SET provider = EXCLUDED.provider,
            encrypted_key = EXCLUDED.encrypted_key,
            key_suffix = EXCLUDED.key_suffix,
            updated_at = now()
        """,
        user_id,
        provider,
        encrypt_key(raw_key),
        key_suffix(raw_key),
    )


async def get_user_llm_key(conn, user_id: int) -> UserLLMKey | None:
    row = await conn.fetchrow(
        "SELECT provider, encrypted_key, key_suffix FROM user_llm_keys WHERE user_id = $1", user_id
    )
    if row is None:
        return None
    return UserLLMKey(provider=row["provider"], raw_key=decrypt_key(row["encrypted_key"]), key_suffix=row["key_suffix"])


async def delete_user_llm_key(conn, user_id: int) -> None:
    await conn.execute("DELETE FROM user_llm_keys WHERE user_id = $1", user_id)
