from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import transcribe


@pytest.mark.asyncio
async def test_transcribe_bytes_returns_text(monkeypatch):
    fake_result = MagicMock()
    fake_result.text = "Mario gave Marsels 100 euro"
    mock_create = AsyncMock(return_value=fake_result)
    monkeypatch.setattr(
        transcribe._client.audio.transcriptions, "create", mock_create
    )

    text = await transcribe.transcribe_bytes(b"fake-ogg-bytes", "voice.ogg")
    assert text == "Mario gave Marsels 100 euro"
    assert mock_create.await_count == 1


@pytest.mark.asyncio
async def test_transcribe_bytes_empty_result(monkeypatch):
    fake_result = MagicMock()
    fake_result.text = "   "
    monkeypatch.setattr(
        transcribe._client.audio.transcriptions,
        "create",
        AsyncMock(return_value=fake_result),
    )

    text = await transcribe.transcribe_bytes(b"x", "voice.ogg")
    assert text == ""
