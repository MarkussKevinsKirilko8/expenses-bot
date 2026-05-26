from unittest.mock import MagicMock

import pytest

from app.services import sheets
from app.services.sheets import SheetUser


def test_sanitize_title_strips_invalid_chars():
    assert sheets._sanitize_title("Ma/rio:[x]") == "Mariox"
    assert sheets._sanitize_title("  Bob  ") == "Bob"


def test_sanitize_title_truncates_long_names():
    assert len(sheets._sanitize_title("x" * 200)) <= 90


def test_append_expense_writes_row_in_column_order(monkeypatch):
    captured = {}
    fake_ws = MagicMock()

    def _capture(row, **kw):
        captured["row"] = row
        captured["opts"] = kw

    fake_ws.append_row.side_effect = _capture
    monkeypatch.setattr(sheets, "_worksheet_for", lambda user: fake_ws)

    user = SheetUser(id=111, first_name="Mario", username=None)
    sheets.append_expense(
        user,
        {
            "category": "groceries",
            "amount": -100,
            "currency": "EUR",
            "description": "weekly shop",
            "raw_text": "used 100 euro on groceries",  # present but must NOT be written
        },
        now="2026-05-26 14:00",
    )
    # Date, Description, Category, Currency, Amount  (no Raw text)
    assert captured["row"] == [
        "2026-05-26 14:00",
        "weekly shop",
        "groceries",
        "EUR",
        -100,
    ]
    assert captured["opts"].get("value_input_option") == "USER_ENTERED"


def test_read_all_reads_from_users_tab(monkeypatch):
    fake_ws = MagicMock()
    fake_ws.get_all_records.return_value = [{"Amount": -100}]
    monkeypatch.setattr(sheets, "_worksheet_for", lambda user: fake_ws)

    rows = sheets.read_all(SheetUser(id=111, first_name="Mario", username=None))
    assert rows == [{"Amount": -100}]


def test_worksheet_for_new_user_creates_named_tab(monkeypatch):
    created = {}
    fake_ws = MagicMock()
    fake_ss = MagicMock()
    fake_ss.worksheets.return_value = []

    def _add_worksheet(title, rows, cols):
        created["title"] = title
        return fake_ws

    fake_ss.add_worksheet.side_effect = _add_worksheet
    monkeypatch.setattr(sheets, "_spreadsheet", lambda: fake_ss)
    monkeypatch.setattr(sheets, "_load_registry", lambda: {})
    registered = {}
    monkeypatch.setattr(sheets, "_register", lambda uid, title: registered.update({uid: title}))

    sheets._worksheet_for(SheetUser(id=111, first_name="Mario", username="mar"))
    assert created["title"] == "Mario"
    assert registered == {111: "Mario"}
    fake_ws.update.assert_called_once()
    fake_ws.format.assert_called_once()      # header made bold
    fake_ws.freeze.assert_called_once()      # header row frozen


def test_worksheet_for_name_collision_appends_id(monkeypatch):
    created = {}
    fake_ws = MagicMock()
    fake_ss = MagicMock()
    existing = MagicMock()
    existing.title = "Mario"
    fake_ss.worksheets.return_value = [existing]

    def _add(title, rows, cols):
        created["title"] = title
        return fake_ws

    fake_ss.add_worksheet.side_effect = _add
    monkeypatch.setattr(sheets, "_spreadsheet", lambda: fake_ss)
    monkeypatch.setattr(sheets, "_load_registry", lambda: {})
    monkeypatch.setattr(sheets, "_register", lambda uid, title: None)

    sheets._worksheet_for(SheetUser(id=999, first_name="Mario", username=None))
    assert created["title"] == "Mario (999)"


def test_worksheet_for_returning_user_uses_registered_tab(monkeypatch):
    fake_ss = MagicMock()
    fake_ss.worksheet.return_value = "EXISTING_WS"
    monkeypatch.setattr(sheets, "_spreadsheet", lambda: fake_ss)
    monkeypatch.setattr(sheets, "_load_registry", lambda: {"111": "Mario"})

    ws = sheets._worksheet_for(SheetUser(id=111, first_name="Mario", username=None))
    fake_ss.worksheet.assert_called_once_with("Mario")
    assert ws == "EXISTING_WS"


def test_fallback_to_username_then_id(monkeypatch):
    created = {}
    fake_ws = MagicMock()
    fake_ss = MagicMock()
    fake_ss.worksheets.return_value = []

    def _add(title, rows, cols):
        created["title"] = title
        return fake_ws

    fake_ss.add_worksheet.side_effect = _add
    monkeypatch.setattr(sheets, "_spreadsheet", lambda: fake_ss)
    monkeypatch.setattr(sheets, "_load_registry", lambda: {})
    monkeypatch.setattr(sheets, "_register", lambda uid, title: None)

    sheets._worksheet_for(SheetUser(id=222, first_name=None, username="cooluser"))
    assert created["title"] == "cooluser"
