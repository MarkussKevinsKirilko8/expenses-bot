from unittest.mock import MagicMock

import gspread

from app.services import sheets
from app.services.sheets import SheetUser


def test_sanitize_title_strips_invalid_chars():
    assert sheets._sanitize_title("Ma/rio:[x]") == "Mariox"
    assert sheets._sanitize_title("  Bob  ") == "Bob"


def test_sanitize_title_truncates_long_names():
    assert len(sheets._sanitize_title("x" * 200)) <= sheets._MAX_TITLE_LEN


def test_resolve_base_new_user(monkeypatch):
    monkeypatch.setattr(sheets, "_load_registry", lambda: {})
    base = sheets._resolve_base(SheetUser(id=1, first_name="Mario", username=None))
    assert base == "Mario"


def test_resolve_base_collision_appends_id(monkeypatch):
    monkeypatch.setattr(
        sheets, "_load_registry", lambda: {"1": {"base": "Mario", "tabs": {}}}
    )
    base = sheets._resolve_base(SheetUser(id=2, first_name="Mario", username=None))
    assert base == "Mario (2)"


def test_resolve_base_returning_user_keeps_original(monkeypatch):
    monkeypatch.setattr(
        sheets,
        "_load_registry",
        lambda: {"5": {"base": "Bob", "tabs": {"EUR": "Bob EUR"}}},
    )
    base = sheets._resolve_base(SheetUser(id=5, first_name="Robert", username=None))
    assert base == "Bob"


def test_resolve_base_falls_back_to_username_then_id(monkeypatch):
    monkeypatch.setattr(sheets, "_load_registry", lambda: {})
    assert sheets._resolve_base(SheetUser(id=9, first_name=None, username="cool")) == "cool"
    assert sheets._resolve_base(SheetUser(id=9, first_name=None, username=None)) == "9"


def test_append_expense_routes_by_currency_and_inserts_on_top(monkeypatch):
    captured = {}
    fake_ws = MagicMock()

    def _insert(values, index, **kw):
        captured["values"] = values
        captured["index"] = index
        captured["opts"] = kw

    fake_ws.insert_row.side_effect = _insert

    routed = {}

    def _ws_for(user, currency):
        routed["currency"] = currency
        return fake_ws

    monkeypatch.setattr(sheets, "_worksheet_for", _ws_for)

    user = SheetUser(id=1, first_name="Mario", username=None)
    sheets.append_expense(
        user,
        {
            "amount": -100,
            "currency": "eur",  # lower-case on purpose
            "description": "wallet",
            "category": "shopping",
            "raw_text": "ignored",
        },
        now="2026-05-26 14:00",
    )

    assert routed["currency"] == "EUR"  # normalised to upper-case
    # Amount, Description, Date, Category (no currency column)
    assert captured["values"] == [-100, "wallet", "2026-05-26 14:00", "shopping"]
    assert captured["index"] == sheets.DATA_START_ROW  # newest on top
    assert captured["opts"].get("value_input_option") == "USER_ENTERED"


def test_worksheet_for_creates_named_currency_tab(monkeypatch):
    created = {}
    fake_ws = MagicMock()
    fake_ss = MagicMock()
    fake_ss.worksheets.return_value = []

    def _add(title, rows, cols):
        created["title"] = title
        return fake_ws

    fake_ss.add_worksheet.side_effect = _add
    # No existing tab with that title -> forces creation.
    fake_ss.worksheet.side_effect = gspread.WorksheetNotFound
    monkeypatch.setattr(sheets, "_spreadsheet", lambda: fake_ss)
    monkeypatch.setattr(sheets, "_load_registry", lambda: {})
    monkeypatch.setattr(sheets, "_resolve_base", lambda user: "Mario")
    monkeypatch.setattr(sheets, "_new_tab_layout", lambda ws: None)
    registered = {}
    monkeypatch.setattr(
        sheets, "_register",
        lambda uid, base, cur, title: registered.update({"row": (uid, base, cur, title)}),
    )

    ws = sheets._worksheet_for(SheetUser(id=1, first_name="Mario", username=None), "EUR")
    assert created["title"] == "Mario EUR"
    assert registered["row"] == (1, "Mario", "EUR", "Mario EUR")
    assert ws is fake_ws


def test_worksheet_for_returning_currency_uses_registered_tab(monkeypatch):
    fake_ss = MagicMock()
    fake_ss.worksheet.return_value = "EXISTING"
    monkeypatch.setattr(sheets, "_spreadsheet", lambda: fake_ss)
    monkeypatch.setattr(
        sheets,
        "_load_registry",
        lambda: {"1": {"base": "Mario", "tabs": {"EUR": "Mario EUR"}}},
    )

    ws = sheets._worksheet_for(SheetUser(id=1, first_name="Mario", username=None), "EUR")
    fake_ss.worksheet.assert_called_once_with("Mario EUR")
    assert ws == "EXISTING"


def test_read_all_gathers_currencies_and_injects_currency(monkeypatch):
    eur_ws = MagicMock()
    eur_ws.get_all_values.return_value = [
        ["Amount", "Description", "Date", "Category"],       # row 1 header
        ["=SUM(A3:A)"],                                      # row 2 total
        ["-100", "wallet", "2026-05-26 14:00", "shopping"],  # row 3 data
    ]
    usd_ws = MagicMock()
    usd_ws.get_all_values.return_value = [
        ["Amount", "Description", "Date", "Category"],
        ["=SUM(A3:A)"],
        ["50", "tip", "2026-05-27 10:00", ""],
    ]

    def _ws(title):
        return {"Mario EUR": eur_ws, "Mario USD": usd_ws}[title]

    fake_ss = MagicMock()
    fake_ss.worksheet.side_effect = _ws
    monkeypatch.setattr(sheets, "_spreadsheet", lambda: fake_ss)
    monkeypatch.setattr(
        sheets,
        "_load_registry",
        lambda: {"1": {"base": "Mario", "tabs": {"EUR": "Mario EUR", "USD": "Mario USD"}}},
    )

    rows = sheets.read_all(SheetUser(id=1, first_name="Mario", username=None))
    assert len(rows) == 2
    assert {
        "Amount": "-100", "Description": "wallet", "Date": "2026-05-26 14:00",
        "Category": "shopping", "Currency": "EUR",
    } in rows
    assert {
        "Amount": "50", "Description": "tip", "Date": "2026-05-27 10:00",
        "Category": "", "Currency": "USD",
    } in rows


def test_read_all_unknown_user_returns_empty(monkeypatch):
    monkeypatch.setattr(sheets, "_load_registry", lambda: {})
    assert sheets.read_all(SheetUser(id=404, first_name="Ghost", username=None)) == []
