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
async def test_match_person_returns_matched_id(monkeypatch):
    monkeypatch.setattr(
        brain._client.messages, "create", AsyncMock(return_value=_fake_response("222"))
    )
    out = await brain.match_person("andrei", [("222", "Андрей", ["andrei"]), ("333", "Bob", [])])
    assert out == "222"


@pytest.mark.asyncio
async def test_match_person_none_when_unsure(monkeypatch):
    monkeypatch.setattr(
        brain._client.messages, "create", AsyncMock(return_value=_fake_response("none"))
    )
    out = await brain.match_person("xyz", [("222", "Андрей", [])])
    assert out is None


@pytest.mark.asyncio
async def test_match_person_rejects_id_not_in_candidates(monkeypatch):
    monkeypatch.setattr(
        brain._client.messages, "create", AsyncMock(return_value=_fake_response("999"))
    )
    out = await brain.match_person("andrei", [("222", "Андрей", [])])
    assert out is None  # 999 isn't a candidate -> rejected


@pytest.mark.asyncio
async def test_match_person_no_candidates_skips_call():
    assert await brain.match_person("andrei", []) is None


@pytest.mark.asyncio
async def test_classify_and_extract_returns_signed_log(monkeypatch):
    payload = {
        "type": "log",
        "category": "",
        "amount": -100,
        "currency": "EUR",
        "description": "new wallet",
    }
    mock_create = AsyncMock(return_value=_fake_response(json.dumps(payload)))
    monkeypatch.setattr(brain._client.messages, "create", mock_create)

    result = await brain.classify_and_extract("used 100 euro on a new wallet")
    assert result["type"] == "log"
    assert result["amount"] == -100
    assert result["currency"] == "EUR"
    assert result["category"] == ""
    assert "person" not in result


@pytest.mark.asyncio
async def test_classify_and_extract_passes_through_counterparty(monkeypatch):
    payload = {
        "type": "log", "category": "", "amount": -100, "currency": "EUR",
        "description": "groceries", "counterparty": "Marsels",
    }
    monkeypatch.setattr(
        brain._client.messages, "create",
        AsyncMock(return_value=_fake_response(json.dumps(payload))),
    )
    result = await brain.classify_and_extract("I gave Marsels 100 euro for groceries")
    assert result["counterparty"] == "Marsels"
    assert result["amount"] == -100


@pytest.mark.asyncio
async def test_classify_and_extract_positive_amount(monkeypatch):
    payload = {"type": "log", "category": "", "amount": 300, "currency": "EUR", "description": "from Marsels"}
    monkeypatch.setattr(
        brain._client.messages, "create",
        AsyncMock(return_value=_fake_response(json.dumps(payload))),
    )
    result = await brain.classify_and_extract("Marsels gave me 300 euro")
    assert result["amount"] == 300


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
