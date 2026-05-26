import logging
from collections import namedtuple
from datetime import datetime
from functools import lru_cache
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from app.config.settings import settings

logger = logging.getLogger(__name__)

# Per currency-tab columns. The currency itself is the tab name, not a column.
HEADER = ["Amount", "Description", "Date", "Category"]
HEADER_ROW = 3        # row 1 = total, row 2 = blank spacer, row 3 = header
DATA_START_ROW = 4    # newest expenses are inserted here (older rows shift down)
_COLUMN_WIDTHS = [110, 320, 150, 140]  # Amount, Description, Date, Category

REGISTRY_TITLE = "_registry"
_INVALID_TITLE_CHARS = set(r"/\?*[]:")
_MAX_TITLE_LEN = 80  # leave headroom for the " <CURRENCY>" suffix

# Number format that shows negatives in red (applied to the Amount column).
_RED_NEG = {"numberFormat": {"type": "NUMBER", "pattern": "0.##;[Red]-0.##"}}

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Lightweight user identity passed in from the handlers.
SheetUser = namedtuple("SheetUser", ["id", "first_name", "username"])

# In-memory registry cache:
#   {str(user_id): {"base": <clean name>, "tabs": {<CURRENCY>: <tab title>}}}
# Durable source of truth is the _registry tab (survives restarts).
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


# --- registry --------------------------------------------------------------

def _registry_ws():
    ss = _spreadsheet()
    try:
        return ss.worksheet(REGISTRY_TITLE)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=REGISTRY_TITLE, rows=1000, cols=4)
        ws.update("A1", [["user_id", "base", "currency", "tab_title"]])
        return ws


def _load_registry() -> dict:
    global _registry_cache
    if _registry_cache is None:
        cache: dict = {}
        for r in _registry_ws().get_all_records():
            uid = str(r.get("user_id", "")).strip()
            if not uid:
                continue
            entry = cache.setdefault(uid, {"base": "", "tabs": {}})
            if r.get("base"):
                entry["base"] = r["base"]
            currency = str(r.get("currency", "")).strip().upper()
            if currency:
                entry["tabs"][currency] = r.get("tab_title", "")
        _registry_cache = cache
    return _registry_cache


def _register(user_id: int, base: str, currency: str, title: str) -> None:
    reg = _load_registry()
    entry = reg.setdefault(str(user_id), {"base": base, "tabs": {}})
    entry["base"] = base
    entry["tabs"][currency] = title
    _registry_ws().append_row(
        [str(user_id), base, currency, title], value_input_option="RAW"
    )


def _resolve_base(user: SheetUser) -> str:
    """The user's clean tab-name prefix (e.g. 'Mario'), stable across sessions."""
    reg = _load_registry()
    existing = reg.get(str(user.id))
    if existing and existing.get("base"):
        return existing["base"]
    desired = (
        _sanitize_title(user.first_name or "")
        or _sanitize_title(user.username or "")
        or str(user.id)
    )
    taken = {
        v["base"]
        for k, v in reg.items()
        if k != str(user.id) and v.get("base")
    }
    if desired in taken:
        return f"{desired} ({user.id})"
    return desired


# --- worksheet routing -----------------------------------------------------

def _new_tab_layout(ws) -> None:
    """Lay out a freshly created currency tab: live total on row 1, blank row 2,
    bold frozen header on row 3, roomy columns, negative amounts in red."""
    last_col = chr(ord("A") + len(HEADER) - 1)
    try:
        ws.update(f"A{HEADER_ROW}", [HEADER])
        ws.format(
            f"A{HEADER_ROW}:{last_col}{HEADER_ROW}", {"textFormat": {"bold": True}}
        )
        # Live total of every amount below the header (A1 is not in the summed range).
        ws.update(
            "A1", [[f"=SUM(A{DATA_START_ROW}:A)"]], value_input_option="USER_ENTERED"
        )
        ws.format("A1", {"textFormat": {"bold": True}})
        ws.freeze(rows=HEADER_ROW)
        # Red negatives across the whole Amount column (covers the total + future rows).
        ws.format("A:A", _RED_NEG)
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
        logger.exception("failed to lay out new tab")


def _existing_titles() -> set:
    return {ws.title for ws in _spreadsheet().worksheets()}


def _worksheet_for(user: SheetUser, currency: str):
    """Get or create the user's tab for this currency (e.g. 'Mario EUR')."""
    reg = _load_registry()
    entry = reg.get(str(user.id))
    if entry and currency in entry.get("tabs", {}):
        try:
            return _spreadsheet().worksheet(entry["tabs"][currency])
        except gspread.WorksheetNotFound:
            pass  # registered tab was deleted; recreate below

    base = _resolve_base(user)
    title = f"{base} {currency}".strip()
    if title in _existing_titles():
        title = f"{base} {currency} ({user.id})"
    ws = _spreadsheet().add_worksheet(title=title, rows=1000, cols=len(HEADER))
    _new_tab_layout(ws)
    _register(user.id, base, currency, title)
    return ws


# --- public API ------------------------------------------------------------

def append_expense(user: SheetUser, fields: dict, now: Optional[str] = None) -> None:
    currency = (fields.get("currency") or "").strip().upper() or "?"
    timestamp = now or datetime.now().strftime("%Y-%m-%d %H:%M")
    row = [
        fields.get("amount", ""),
        fields.get("description", ""),
        timestamp,
        fields.get("category", ""),
    ]
    # Newest on top: insert right under the header, pushing older rows down.
    _worksheet_for(user, currency).insert_row(
        row, index=DATA_START_ROW, value_input_option="USER_ENTERED"
    )


def read_all(user: SheetUser) -> list:
    """Every expense for the user across all their currency tabs. Each row is
    tagged with its Currency (from the tab name) so the AI can answer per
    currency, even though the sheet itself has no currency column."""
    reg = _load_registry()
    entry = reg.get(str(user.id))
    if not entry:
        return []
    ss = _spreadsheet()
    records = []
    width = len(HEADER)
    for currency, title in entry.get("tabs", {}).items():
        try:
            ws = ss.worksheet(title)
        except gspread.WorksheetNotFound:
            continue
        for row in ws.get_all_values()[DATA_START_ROW - 1:]:
            cells = (row + [""] * width)[:width]
            if not any(c != "" for c in cells):
                continue
            record = dict(zip(HEADER, cells))
            record["Currency"] = currency
            records.append(record)
    return records
