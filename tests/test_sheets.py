from unittest.mock import MagicMock

from app.services import sheets


def test_append_expense_writes_row_in_column_order(monkeypatch):
    captured = {}

    fake_ws = MagicMock()
    fake_ws.append_row.side_effect = lambda row, **kw: captured.setdefault("row", row)
    monkeypatch.setattr(sheets, "_worksheet", lambda: fake_ws)

    sheets.append_expense(
        {
            "person": "Mario",
            "category": "groceries",
            "amount": 100,
            "currency": "EUR",
            "description": "weekly shop",
            "raw_text": "Mario gave Marsels 100 euro for groceries",
        },
        now="2026-05-26 14:00",
    )

    # Date, Person, Category, Amount, Currency, Description, Raw text
    assert captured["row"] == [
        "2026-05-26 14:00",
        "Mario",
        "groceries",
        100,
        "EUR",
        "weekly shop",
        "Mario gave Marsels 100 euro for groceries",
    ]


def test_read_all_returns_list_of_dicts(monkeypatch):
    fake_ws = MagicMock()
    fake_ws.get_all_records.return_value = [
        {"Date": "2026-05-26", "Person": "Mario", "Amount": 100}
    ]
    monkeypatch.setattr(sheets, "_worksheet", lambda: fake_ws)

    rows = sheets.read_all()
    assert rows == [{"Date": "2026-05-26", "Person": "Mario", "Amount": 100}]
