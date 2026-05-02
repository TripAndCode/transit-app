from unittest.mock import MagicMock, patch

import pytest

from pipeline.query.formatter import _reset_groq_client, format_unknown


@pytest.fixture(autouse=True)
def reset_client():
    _reset_groq_client()
    yield
    _reset_groq_client()


def _make_streaming_mock(chunks: list[str]):
    mock_client = MagicMock()
    mock_chunks = []
    for text in chunks:
        mock_delta = MagicMock()
        mock_delta.content = text
        mock_choice = MagicMock()
        mock_choice.delta = mock_delta
        mock_chunk = MagicMock()
        mock_chunk.choices = [mock_choice]
        mock_chunks.append(mock_chunk)
    mock_client.chat.completions.create.return_value = iter(mock_chunks)
    return mock_client


@pytest.mark.asyncio
async def test_format_unknown_concatenates_chunks():
    mock_client = _make_streaming_mock(["申し", "訳ありません", "、分かりません。"])
    with patch("pipeline.query.formatter._get_groq_client", return_value=mock_client):
        result = await format_unknown("何ですか？")
    assert result == "申し訳ありません、分かりません。"


@pytest.mark.asyncio
async def test_format_unknown_handles_none_content():
    mock_client = _make_streaming_mock(["answer", None, "!"])
    # Override the mock to return None for the middle chunk
    mock_chunks = list(mock_client.chat.completions.create.return_value)
    mock_chunks[1].choices[0].delta.content = None
    mock_client.chat.completions.create.return_value = iter(mock_chunks)
    with patch("pipeline.query.formatter._get_groq_client", return_value=mock_client):
        result = await format_unknown("test")
    assert result == "answer!"
