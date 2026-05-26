from datetime import datetime
from functools import lru_cache
from typing import Any, Optional

import gspread
from google.oauth2.service_account import Credentials

from app.config.settings import settings

HEADER = ["Date", "Person", "Category", "Amount", "Currency", "Description", "Raw text"]

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


@lru_cache(maxsize=1)
def _client() -> gspread.Client:
    creds = Credentials.from_service_account_file(
        settings.google_creds_path, scopes=_SCOPES
    )
    return gspread.authorize(creds)


def _worksheet():
    sheet = _client().open_by_key(settings.google_sheet_id)
    ws = sheet.sheet1
    # Ensure the header row exists exactly once.
    existing = ws.row_values(1)
    if existing != HEADER:
        ws.update("A1", [HEADER])
    return ws


def append_expense(fields: dict[str, Any], now: Optional[str] = None) -> None:
    timestamp = now or datetime.now().strftime("%Y-%m-%d %H:%M")
    row = [
        timestamp,
        fields.get("person", ""),
        fields.get("category", ""),
        fields.get("amount", ""),
        fields.get("currency", ""),
        fields.get("description", ""),
        fields.get("raw_text", ""),
    ]
    _worksheet().append_row(row, value_input_option="USER_ENTERED")


def read_all() -> list[dict[str, Any]]:
    return _worksheet().get_all_records()
