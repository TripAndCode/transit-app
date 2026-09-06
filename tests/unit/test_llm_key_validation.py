from unittest.mock import AsyncMock, patch

import pytest

from pipeline.query.llm_key_validation import validate_provider_key


@pytest.mark.asyncio
async def test_valid_key_returns_true():
    with patch("openai.AsyncOpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create = AsyncMock(return_value=object())
        mock_openai.return_value.close = AsyncMock()
        assert await validate_provider_key("groq", "gsk_valid") is True


@pytest.mark.asyncio
async def test_invalid_key_returns_false():
    import openai

    # openai.AuthenticationError's real __init__ requires a genuine
    # httpx.Response (it reads `.request` off it), which isn't available in
    # a pure mock. `validate_provider_key` only type-checks the exception
    # class, so a bare instance with `__init__` bypassed is sufficient —
    # matches the same bypass pattern used in tests/query/test_llm_client.py.
    class _FakeAuthenticationError(openai.AuthenticationError):
        def __init__(self):
            pass

    with patch("openai.AsyncOpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create = AsyncMock(side_effect=_FakeAuthenticationError())
        mock_openai.return_value.close = AsyncMock()
        assert await validate_provider_key("groq", "gsk_bad") is False


@pytest.mark.asyncio
async def test_permission_denied_returns_false():
    import openai

    class _FakePermissionDeniedError(openai.PermissionDeniedError):
        def __init__(self):
            pass

    with patch("openai.AsyncOpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create = AsyncMock(
            side_effect=_FakePermissionDeniedError()
        )
        mock_openai.return_value.close = AsyncMock()
        assert await validate_provider_key("groq", "gsk_revoked") is False


@pytest.mark.asyncio
async def test_connection_error_propagates_instead_of_reporting_valid():
    import openai

    class _FakeAPIConnectionError(openai.APIConnectionError):
        def __init__(self):
            pass

    with patch("openai.AsyncOpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create = AsyncMock(
            side_effect=_FakeAPIConnectionError()
        )
        mock_openai.return_value.close = AsyncMock()
        with pytest.raises(openai.APIConnectionError):
            await validate_provider_key("groq", "gsk_valid")


@pytest.mark.asyncio
async def test_other_api_error_returns_true():
    import openai

    class _FakeRateLimitError(openai.RateLimitError):
        def __init__(self):
            pass

    with patch("openai.AsyncOpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create = AsyncMock(
            side_effect=_FakeRateLimitError()
        )
        mock_openai.return_value.close = AsyncMock()
        assert await validate_provider_key("groq", "gsk_valid") is True


@pytest.mark.asyncio
async def test_unsupported_provider_returns_false():
    assert await validate_provider_key("not_a_provider", "x") is False
