from unittest.mock import MagicMock

import gspread

from app.services import sheets
from app.services.sheets import SheetUser


def test_sanitize_title_strips_invalid_chars():
    assert sheets._sanitize_title("Ma/rio:[x]") == "Mariox"
    assert sheets._sanitize_title("  Bob  ") == "Bob"


def test_resolve_base_new_user(monkeypatch):
    monkeypatch.setattr(sheets, "_load_registry", lambda: {})
    assert sheets._resolve_base(SheetUser(1, "Mario", None)) == "Mario"


def test_resolve_base_collision_appends_id(monkeypatch):
    monkeypatch.setattr(
        sheets, "_load_registry", lambda: {"1": {"base": "Mario", "title": "Mario"}}
    )
    assert sheets._resolve_base(SheetUser(2, "Mario", None)) == "Mario (2)"


def test_resolve_base_returning_user_keeps_original(monkeypatch):
    monkeypatch.setattr(
        sheets, "_load_registry", lambda: {"5": {"base": "Bob", "title": "Bob"}}
    )
    assert sheets._resolve_base(SheetUser(5, "Robert", None)) == "Bob"


def test_find_user_ids_by_name_case_insensitive(monkeypatch):
    monkeypatch.setattr(
        sheets,
        "_load_registry",
        lambda: {
            "1": {"base": "Mario", "title": "Mario", "nicknames": ["Маша", "Marik"]},
            "2": {"base": "mario", "title": "mario (2)", "nicknames": []},
            "3": {"base": "Bob", "title": "Bob", "nicknames": []},
        },
    )
    assert sorted(sheets.find_user_ids_by_name("MARIO")) == ["1", "2"]
    assert sheets.find_user_ids_by_name("nobody") == []
    # matches a team-set nickname too
    assert sheets.find_user_ids_by_name("marik") == ["1"]
    assert sheets.find_user_ids_by_name("Маша") == ["1"]


def test_user_for_reconstructs_known_user(monkeypatch):
    monkeypatch.setattr(
        sheets, "_load_registry", lambda: {"7": {"base": "Mario", "title": "Mario"}}
    )
    assert sheets.user_for(7) == SheetUser(id=7, first_name="Mario", username=None)
    assert sheets.user_for(404) is None


def test_worksheet_for_creates_tab(monkeypatch):
    created = {}
    fake_ws = MagicMock()
    fake_ss = MagicMock()
    fake_ss.worksheet.side_effect = gspread.WorksheetNotFound

    def _add(title, rows, cols):
        created["title"] = title
        return fake_ws

    fake_ss.add_worksheet.side_effect = _add
    monkeypatch.setattr(sheets, "_spreadsheet", lambda: fake_ss)
    monkeypatch.setattr(sheets, "_load_registry", lambda: {})
    monkeypatch.setattr(sheets, "_resolve_base", lambda user: "Mario")
    monkeypatch.setattr(sheets, "_new_tab_layout", lambda ws: None)
    registered = {}
    monkeypatch.setattr(
        sheets, "_register", lambda uid, base, title: registered.update({"r": (uid, base, title)})
    )

    ws = sheets._worksheet_for(SheetUser(1, "Mario", None))
    assert created["title"] == "Mario"
    assert registered["r"] == (1, "Mario", "Mario")
    assert ws is fake_ws


def test_append_builds_totals_newest_on_top(monkeypatch):
    captured = {}
    fake_ws = MagicMock()
    fake_ws.get_values.return_value = [
        ["Amount", "Currency", "Description", "Date", "Category"],
        [50.0, "EUR", "", "", ""],  # stale total row (no Date) -> ignored
        [-100.0, "EUR", "wallet", "2026-05-26 14:00", "shopping"],
    ]

    def _update(values, range_name=None, **kw):
        captured["values"] = values
        captured["opts"] = kw

    fake_ws.update.side_effect = _update
    monkeypatch.setattr(sheets, "_worksheet_for", lambda user: fake_ws)

    sheets.append_expense(
        SheetUser(1, "Mario", None),
        {"amount": 30, "currency": "eur", "description": "lunch", "category": "food"},
        now="2026-05-27 12:00",
    )

    body = captured["values"]
    assert body[0] == [-70.0, "EUR", "", "", ""]  # EUR total = 30 + (-100)
    assert body[1] == [30.0, "EUR", "lunch", "2026-05-27 12:00", "food"]  # newest on top
    assert body[2] == [-100.0, "EUR", "wallet", "2026-05-26 14:00", "shopping"]
    assert captured["opts"].get("value_input_option") == "RAW"
    fake_ws.freeze.assert_called_with(rows=2)


def test_append_multi_currency_makes_two_totals(monkeypatch):
    captured = {}
    fake_ws = MagicMock()
    fake_ws.get_values.return_value = [
        ["Amount", "Currency", "Description", "Date", "Category"],
        [-100.0, "EUR", "a", "2026-05-26 10:00", ""],
        [50.0, "USD", "b", "2026-05-26 11:00", ""],
    ]
    fake_ws.update.side_effect = lambda values, range_name=None, **kw: captured.update(values=values)
    monkeypatch.setattr(sheets, "_worksheet_for", lambda user: fake_ws)

    sheets.append_expense(
        SheetUser(1, "Mario", None),
        {"amount": 30, "currency": "USD", "description": "c", "category": ""},
        now="2026-05-27 12:00",
    )
    body = captured["values"]
    # two total rows, sorted by currency
    assert body[0] == [-100.0, "EUR", "", "", ""]
    assert body[1] == [80.0, "USD", "", "", ""]  # 30 + 50
    fake_ws.freeze.assert_called_with(rows=3)


def test_read_all_excludes_total_rows(monkeypatch):
    fake_ws = MagicMock()
    fake_ws.get_values.return_value = [
        ["Amount", "Currency", "Description", "Date", "Category"],
        [-70.0, "EUR", "", "", ""],  # total -> excluded
        [30.0, "EUR", "lunch", "2026-05-27 12:00", "food"],
    ]
    fake_ss = MagicMock()
    fake_ss.worksheet.return_value = fake_ws
    monkeypatch.setattr(sheets, "_spreadsheet", lambda: fake_ss)
    monkeypatch.setattr(
        sheets, "_load_registry", lambda: {"1": {"base": "Mario", "title": "Mario"}}
    )

    rows = sheets.read_all(SheetUser(1, "Mario", None))
    assert rows == [
        {
            "Amount": 30.0, "Currency": "EUR", "Description": "lunch",
            "Date": "2026-05-27 12:00", "Category": "food",
        }
    ]
