import os

os.environ.setdefault("LLM_KEY_ENCRYPTION_KEY", "zJj1v3nq7v3rj0aWq2p8m9s4b6d5f7h9k1n3q5s7u9w=")

import pytest
from cryptography.fernet import Fernet

from pipeline.query.user_llm_keys import (
    delete_user_llm_key,
    get_user_llm_key,
    save_user_llm_key,
)


@pytest.fixture
async def user_id(aconn):
    row = await aconn.fetchrow(
        "INSERT INTO users (email, name, role) VALUES ('byok@test', 'BYOK', 'user') RETURNING user_id"
    )
    return row["user_id"]


async def test_save_then_get_roundtrips(aconn, user_id):
    await save_user_llm_key(aconn, user_id, "groq", "gsk_test_abcd1234")
    key = await get_user_llm_key(aconn, user_id)
    assert key is not None
    assert key.provider == "groq"
    assert key.raw_key == "gsk_test_abcd1234"
    assert key.key_suffix == "1234"


async def test_save_upserts_on_conflict(aconn, user_id):
    await save_user_llm_key(aconn, user_id, "groq", "gsk_first_0000")
    await save_user_llm_key(aconn, user_id, "openai", "sk_second_1111")
    key = await get_user_llm_key(aconn, user_id)
    assert key.provider == "openai"
    assert key.raw_key == "sk_second_1111"


async def test_get_returns_none_when_no_key_stored(aconn, user_id):
    assert await get_user_llm_key(aconn, user_id) is None


async def test_delete_removes_the_key(aconn, user_id):
    await save_user_llm_key(aconn, user_id, "cerebras", "csk_test_2222")
    await delete_user_llm_key(aconn, user_id)
    assert await get_user_llm_key(aconn, user_id) is None


async def test_get_degrades_gracefully_on_undecryptable_key(aconn, user_id):
    await save_user_llm_key(aconn, user_id, "groq", "gsk_test_abcd1234")
    # Simulate a rotated encryption key / corrupted ciphertext: overwrite the
    # stored blob with bytes that can never decrypt under the current Fernet
    # key, and confirm the caller sees "no key configured" rather than a
    # crash.
    fernet = Fernet(os.environ["LLM_KEY_ENCRYPTION_KEY"].encode())
    bogus = bytearray(fernet.encrypt(b"unrelated-payload"))
    bogus[-1] ^= 0xFF
    await aconn.execute(
        "UPDATE user_llm_keys SET encrypted_key = $1 WHERE user_id = $2",
        bytes(bogus),
        user_id,
    )
    assert await get_user_llm_key(aconn, user_id) is None
