import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import brain


def _fake_response(text: str) -> MagicMock:
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


@pytest.mark.asyncio
async def test_classify_and_extract_returns_log(monkeypatch):
    payload = {
        "type": "log",
        "person": "Mario",
        "category": "groceries",
        "amount": 100,
        "currency": "EUR",
        "description": "weekly shop",
    }
    mock_create = AsyncMock(return_value=_fake_response(json.dumps(payload)))
    monkeypatch.setattr(brain._client.messages, "create", mock_create)

    result = await brain.classify_and_extract("Mario gave Marsels 100 euro for groceries")
    assert result["type"] == "log"
    assert result["amount"] == 100
    assert result["person"] == "Mario"


@pytest.mark.asyncio
async def test_classify_and_extract_returns_query(monkeypatch):
    mock_create = AsyncMock(return_value=_fake_response('{"type": "query"}'))
    monkeypatch.setattr(brain._client.messages, "create", mock_create)

    result = await brain.classify_and_extract("how much did Mario spend this month?")
    assert result["type"] == "query"


@pytest.mark.asyncio
async def test_classify_and_extract_invalid_json_returns_unclear(monkeypatch):
    mock_create = AsyncMock(return_value=_fake_response("not json at all"))
    monkeypatch.setattr(brain._client.messages, "create", mock_create)

    result = await brain.classify_and_extract("blah")
    assert result["type"] == "unclear"


@pytest.mark.asyncio
async def test_answer_query_returns_text(monkeypatch):
    mock_create = AsyncMock(return_value=_fake_response("Mario spent 340 EUR."))
    monkeypatch.setattr(brain._client.messages, "create", mock_create)

    answer = await brain.answer_query(
        "how much did Mario spend?",
        [{"Person": "Mario", "Amount": 340}],
    )
    assert answer == "Mario spent 340 EUR."


@pytest.mark.asyncio
async def test_classify_and_extract_api_error_returns_unclear(monkeypatch):
    mock_create = AsyncMock(side_effect=RuntimeError("api down"))
    monkeypatch.setattr(brain._client.messages, "create", mock_create)

    result = await brain.classify_and_extract("Mario gave 100 euro")
    assert result["type"] == "unclear"


@pytest.mark.asyncio
async def test_classify_and_extract_unknown_type_returns_unclear(monkeypatch):
    mock_create = AsyncMock(return_value=_fake_response('{"type": "something_else"}'))
    monkeypatch.setattr(brain._client.messages, "create", mock_create)

    result = await brain.classify_and_extract("blah")
    assert result["type"] == "unclear"
