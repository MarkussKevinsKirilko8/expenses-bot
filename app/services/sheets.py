import logging
from collections import namedtuple
from datetime import datetime
from functools import lru_cache
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from app.config.settings import settings

logger = logging.getLogger(__name__)

HEADER = ["Date", "Description", "Category", "Currency", "Amount"]

REGISTRY_TITLE = "_registry"
_INVALID_TITLE_CHARS = set(r"/\?*[]:")
_MAX_TITLE_LEN = 90

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SheetUser = namedtuple("SheetUser", ["id", "first_name", "username"])

_registry_cache: Optional[dict] = None


@lru_cache(maxsize=1)
def _client() -> gspread.Client:
    creds = Credentials.from_service_account_file(
        settings.google_creds_path, scopes=_SCOPES
    )
    return gspread.authorize(creds)


@lru_cache(maxsize=1)
def _spreadsheet():
    return _client().open_by_key(settings.google_sheet_id)


def _sanitize_title(raw: str) -> str:
    cleaned = "".join(c for c in (raw or "") if c not in _INVALID_TITLE_CHARS)
    cleaned = cleaned.strip().strip("'")
    return cleaned[:_MAX_TITLE_LEN]


def _registry_ws():
    ss = _spreadsheet()
    try:
        return ss.worksheet(REGISTRY_TITLE)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=REGISTRY_TITLE, rows=1000, cols=2)
        ws.update("A1", [["user_id", "tab_title"]])
        return ws


def _load_registry() -> dict:
    global _registry_cache
    if _registry_cache is None:
        records = _registry_ws().get_all_records()
        _registry_cache = {str(r["user_id"]): r["tab_title"] for r in records}
    return _registry_cache


def _register(user_id: int, title: str) -> None:
    reg = _load_registry()
    reg[str(user_id)] = title
    _registry_ws().append_row([str(user_id), title], value_input_option="RAW")


def _existing_titles() -> set:
    return {ws.title for ws in _spreadsheet().worksheets()}


# Pixel widths per column, in HEADER order (Date, Description, Category, Currency, Amount).
_COLUMN_WIDTHS = [150, 320, 140, 90, 110]


def _format_new_tab(ws) -> None:
    """Make a freshly created tab readable: bold + frozen header, roomy columns."""
    last_col = chr(ord("A") + len(HEADER) - 1)
    try:
        ws.format(f"A1:{last_col}1", {"textFormat": {"bold": True}})
        ws.freeze(rows=1)
        requests = [
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "COLUMNS",
                        "startIndex": i,
                        "endIndex": i + 1,
                    },
                    "properties": {"pixelSize": width},
                    "fields": "pixelSize",
                }
            }
            for i, width in enumerate(_COLUMN_WIDTHS)
        ]
        ws.spreadsheet.batch_update({"requests": requests})
    except Exception:
        logger.exception("failed to format new tab")


def _worksheet_for(user: SheetUser):
    reg = _load_registry()
    title = reg.get(str(user.id))
    ss = _spreadsheet()
    if title:
        try:
            return ss.worksheet(title)
        except gspread.WorksheetNotFound:
            pass

    desired = (
        _sanitize_title(user.first_name or "")
        or _sanitize_title(user.username or "")
        or str(user.id)
    )

    title = desired
    if title in _existing_titles():
        title = f"{desired} ({user.id})"

    ws = ss.add_worksheet(title=title, rows=1000, cols=len(HEADER))
    ws.update("A1", [HEADER])
    _format_new_tab(ws)
    _register(user.id, title)
    return ws


def append_expense(user: SheetUser, fields: dict, now: Optional[str] = None) -> None:
    timestamp = now or datetime.now().strftime("%Y-%m-%d %H:%M")
    row = [
        timestamp,
        fields.get("description", ""),
        fields.get("category", ""),
        fields.get("currency", ""),
        fields.get("amount", ""),
    ]
    _worksheet_for(user).append_row(row, value_input_option="USER_ENTERED")


def read_all(user: SheetUser) -> list:
    return _worksheet_for(user).get_all_records()
