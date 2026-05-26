import pytest

from app.bot import handlers


def test_format_confirmation_shows_all_fields():
    fields = {
        "person": "Mario",
        "category": "groceries",
        "amount": 100,
        "currency": "EUR",
        "description": "weekly shop",
    }
    text = handlers.format_confirmation(fields)
    assert "Mario" in text
    assert "100" in text
    assert "EUR" in text
    assert "groceries" in text
    assert "weekly shop" in text


def test_build_fields_for_sheet_attaches_raw_text():
    parsed = {
        "type": "log",
        "person": "Mario",
        "category": "groceries",
        "amount": 100,
        "currency": "EUR",
        "description": "weekly shop",
    }
    fields = handlers.build_fields_for_sheet(parsed, raw_text="Mario gave 100")
    assert fields["raw_text"] == "Mario gave 100"
    assert fields["person"] == "Mario"
    assert "type" not in fields
