import pytest

from app.bot import handlers
from app.services.sheets import SheetUser


def test_format_confirmation_no_person_no_pencil():
    fields = {"category": "groceries", "amount": -100, "currency": "EUR", "description": "weekly shop"}
    text = handlers.format_confirmation(fields)
    assert "📝" not in text
    assert "groceries" in text
    assert "-100" in text
    assert "EUR" in text
    assert "weekly shop" in text


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
