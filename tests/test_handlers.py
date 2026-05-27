from unittest.mock import AsyncMock

import pytest

from app.bot import handlers
from app.services import brain, sheets
from app.services.sheets import SheetUser


def test_format_confirmation_no_person_no_pencil():
    fields = {"category": "groceries", "amount": -100, "currency": "EUR", "description": "weekly shop"}
    text = handlers.format_confirmation(fields)
    assert "📝" not in text
    assert "👤" not in text  # no counterparty -> no person line
    assert "groceries" in text
    assert "-100" in text
    assert "EUR" in text
    assert "weekly shop" in text


def test_format_confirmation_shows_counterparty():
    fields = {"counterparty_name": "Marsels", "amount": -100, "currency": "EUR", "description": "groceries"}
    text = handlers.format_confirmation(fields)
    assert "👤 Marsels" in text


@pytest.mark.asyncio
async def test_resolve_counterparty_exact_match(monkeypatch):
    monkeypatch.setattr(sheets, "find_user_ids_by_name", lambda name: ["222"])
    monkeypatch.setattr(sheets, "base_for", lambda uid: "Marsels")
    # exact match -> AI fallback must NOT be called
    monkeypatch.setattr(brain, "match_person", AsyncMock(side_effect=AssertionError("should not run")))
    parsed = {"counterparty": "Marsels", "description": "..."}
    cid, cname = await handlers.resolve_counterparty(parsed, sender_id=111)
    assert (cid, cname) == ("222", "Marsels")


@pytest.mark.asyncio
async def test_resolve_counterparty_ai_fallback_matches(monkeypatch):
    # No exact match, but Claude resolves "andrei" -> Андрей (id 222).
    monkeypatch.setattr(sheets, "find_user_ids_by_name", lambda name: [])
    monkeypatch.setattr(sheets, "all_users", lambda: [("222", "Андрей", ["andrei"])])
    monkeypatch.setattr(sheets, "base_for", lambda uid: "Андрей")
    monkeypatch.setattr(brain, "match_person", AsyncMock(return_value="222"))
    parsed = {"counterparty": "andrei", "description": "..."}
    cid, cname = await handlers.resolve_counterparty(parsed, sender_id=111)
    assert (cid, cname) == ("222", "Андрей")


@pytest.mark.asyncio
async def test_resolve_counterparty_ai_returns_none(monkeypatch):
    monkeypatch.setattr(sheets, "find_user_ids_by_name", lambda name: [])
    monkeypatch.setattr(sheets, "all_users", lambda: [("222", "Андрей", [])])
    monkeypatch.setattr(brain, "match_person", AsyncMock(return_value=None))
    parsed = {"counterparty": "Bob", "description": "..."}
    assert await handlers.resolve_counterparty(parsed, sender_id=111) == (None, None)


@pytest.mark.asyncio
async def test_resolve_counterparty_ambiguous_exact_is_none(monkeypatch):
    # Two exact matches -> never guess, and don't fall through to AI.
    monkeypatch.setattr(sheets, "find_user_ids_by_name", lambda name: ["222", "333"])
    monkeypatch.setattr(brain, "match_person", AsyncMock(side_effect=AssertionError("should not run")))
    parsed = {"counterparty": "Mario", "description": "..."}
    assert await handlers.resolve_counterparty(parsed, sender_id=111) == (None, None)


@pytest.mark.asyncio
async def test_resolve_counterparty_excludes_self(monkeypatch):
    monkeypatch.setattr(sheets, "find_user_ids_by_name", lambda name: ["111"])
    monkeypatch.setattr(sheets, "all_users", lambda: [])
    monkeypatch.setattr(brain, "match_person", AsyncMock(return_value=None))
    parsed = {"counterparty": "Me", "description": "x"}
    assert await handlers.resolve_counterparty(parsed, sender_id=111) == (None, None)


@pytest.mark.asyncio
async def test_resolve_counterparty_no_name():
    parsed = {"counterparty": "", "description": "lunch"}
    assert await handlers.resolve_counterparty(parsed, sender_id=111) == (None, None)


def test_build_mirror_fields_flips_amount_and_fills_me():
    fields = {
        "amount": 456.55, "currency": "EUR",
        "description": "got money from Mario about work", "category": "",
    }
    mirror = handlers.build_mirror_fields(fields, "gave money to {me} about work", "Markuss")
    assert mirror["amount"] == -456.55
    assert mirror["description"] == "Gave money to Markuss about work"  # capitalized
    assert mirror["currency"] == "EUR"


def test_build_mirror_fields_keeps_description_if_no_template():
    fields = {"amount": -10, "currency": "EUR", "description": "x"}
    mirror = handlers.build_mirror_fields(fields, "", "Markuss")
    assert mirror["amount"] == 10
    assert mirror["description"] == "x"  # unchanged when no mirror template


def test_format_confirmation_omits_empty_category():
    fields = {"category": "", "amount": 50, "currency": "USD", "description": "gift"}
    text = handlers.format_confirmation(fields)
    assert "🏷" not in text
    assert "50" in text and "USD" in text


def test_build_fields_for_sheet_no_person():
    parsed = {"type": "log", "category": "fuel", "amount": -30, "currency": "EUR", "description": "diesel"}
    fields = handlers.build_fields_for_sheet(parsed, raw_text="diesel 30 eur")
    assert fields["raw_text"] == "diesel 30 eur"
    assert fields["amount"] == -30
    assert "person" not in fields
    assert "type" not in fields


def test_missing_required_detects_missing_amount_and_currency():
    assert handlers.missing_required({"amount": None, "currency": "EUR"}) == ["amount"]
    assert handlers.missing_required({"amount": -5, "currency": ""}) == ["currency"]
    assert handlers.missing_required({"amount": None, "currency": ""}) == ["amount", "currency"]
    assert handlers.missing_required({"amount": -5, "currency": "EUR"}) == []


def test_build_sheet_user_from_tg_user():
    class FakeUser:
        id = 555
        first_name = "Mario"
        username = "mar"
    su = handlers.build_sheet_user(FakeUser())
    assert su == SheetUser(id=555, first_name="Mario", username="mar")
