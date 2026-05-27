import logging
import threading
from collections import namedtuple
from datetime import datetime
from functools import lru_cache
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import ValueRenderOption

from app.config.settings import settings

logger = logging.getLogger(__name__)

# One tab per user. Currency is a column (right after Amount).
HEADER = ["Amount", "Currency", "Description", "Date", "Category"]
_COLUMN_WIDTHS = [110, 90, 320, 150, 140]
_DATE_COL = 3  # 0-based index of the Date column (used to tell data rows from totals)

REGISTRY_TITLE = "settings"
_INVALID_TITLE_CHARS = set(r"/\?*[]:")
_MAX_TITLE_LEN = 95

# Always 2 decimals, negatives red (decimal separator follows the sheet locale).
_RED_NEG = {"numberFormat": {"type": "NUMBER", "pattern": "0.00;[Red]-0.00"}}
_CENTER = {"horizontalAlignment": "CENTER"}
_LEFT = {"horizontalAlignment": "LEFT"}

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SheetUser = namedtuple("SheetUser", ["id", "first_name", "username"])

# In-memory registry cache: {str(user_id): {"base": <name>, "title": <tab title>}}.
# Durable source of truth is the "settings" tab.
_registry_cache: Optional[dict] = None

# Re-entrant so append_expense can hold it while calling _worksheet_for.
_tab_lock = threading.RLock()


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


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# --- registry --------------------------------------------------------------

def _registry_ws():
    ss = _spreadsheet()
    try:
        return ss.worksheet(REGISTRY_TITLE)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=REGISTRY_TITLE, rows=1000, cols=4)
        ws.update([["user_id", "base", "tab_title", "nicknames"]], "A1")
        return ws


def _split_nicknames(raw) -> list:
    return [n.strip() for n in str(raw or "").split(",") if n.strip()]


def _load_registry() -> dict:
    global _registry_cache
    if _registry_cache is None:
        cache: dict = {}
        for r in _registry_ws().get_all_records():
            uid = str(r.get("user_id", "")).strip()
            if not uid:
                continue
            cache[uid] = {
                "base": r.get("base", "") or "",
                "title": r.get("tab_title", "") or "",
                "nicknames": _split_nicknames(r.get("nicknames", "")),
            }
        _registry_cache = cache
    return _registry_cache


def _refresh_registry() -> dict:
    """Reload from the settings tab so team-edited nicknames are picked up live."""
    global _registry_cache
    _registry_cache = None
    return _load_registry()


def _register(user_id: int, base: str, title: str) -> None:
    reg = _load_registry()
    reg[str(user_id)] = {"base": base, "title": title, "nicknames": []}
    # 4th column (nicknames) left blank for the team to fill in by hand.
    _registry_ws().append_row([str(user_id), base, title, ""], value_input_option="RAW")


def _resolve_base(user: SheetUser) -> str:
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
        v["base"] for k, v in reg.items() if k != str(user.id) and v.get("base")
    }
    if desired in taken:
        return f"{desired} ({user.id})"
    return desired


def find_user_ids_by_name(name: str) -> list:
    """Telegram user IDs whose sheet name OR any team-set nickname matches `name`
    (case-insensitive). Reloads the registry first so newly added nicknames count."""
    needle = (name or "").strip().lower()
    if not needle:
        return []
    out = []
    for uid, v in _refresh_registry().items():
        candidates = [v.get("base", "")] + v.get("nicknames", [])
        if any((c or "").strip().lower() == needle for c in candidates):
            out.append(uid)
    return out


def all_users() -> list:
    """[(user_id, base, [nicknames...]), ...] for every registered user.
    Reloads first so team-edited nicknames are current."""
    return [
        (uid, v.get("base", ""), v.get("nicknames", []))
        for uid, v in _refresh_registry().items()
    ]


def base_for(user_id) -> Optional[str]:
    entry = _load_registry().get(str(user_id))
    return entry["base"] if entry else None


def user_for(user_id) -> Optional[SheetUser]:
    """Reconstruct a SheetUser for a known user_id (for writing to their sheet)."""
    base = base_for(user_id)
    if base is None:
        return None
    return SheetUser(id=int(user_id), first_name=base, username=None)


# --- worksheet routing -----------------------------------------------------

def _new_tab_layout(ws) -> None:
    """Lay out a fresh user tab: bold centered header (row 1, frozen), centered
    values, red 2-decimal amounts. Per-currency total rows are managed on write."""
    last_col = chr(ord("A") + len(HEADER) - 1)
    try:
        ws.update([HEADER], "A1", value_input_option="RAW")
        # Amount + Currency centered; Description/Date/Category left.
        ws.format("A:A", {**_CENTER, **_RED_NEG})
        ws.format("B:B", _CENTER)
        ws.format(f"C:{last_col}", _LEFT)
        # Header stays bold + centered across all columns (overrides the column align on row 1).
        ws.format(f"A1:{last_col}1", {"textFormat": {"bold": True}, **_CENTER})
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
        logger.exception("failed to lay out new tab")


def _cached_tab(user: SheetUser):
    entry = _load_registry().get(str(user.id))
    if entry and entry.get("title"):
        try:
            return _spreadsheet().worksheet(entry["title"])
        except gspread.WorksheetNotFound:
            return None
    return None


def _worksheet_for(user: SheetUser):
    """Get or create the user's single tab (named by their base name)."""
    cached = _cached_tab(user)
    if cached is not None:
        return cached
    with _tab_lock:
        cached = _cached_tab(user)
        if cached is not None:
            return cached
        base = _resolve_base(user)
        ss = _spreadsheet()
        try:
            ws = ss.worksheet(base)  # title is unique per user -> reuse if present
        except gspread.WorksheetNotFound:
            ws = ss.add_worksheet(title=base, rows=1000, cols=len(HEADER))
            _new_tab_layout(ws)
        entry = _load_registry().get(str(user.id))
        if not (entry and entry.get("title") == base):
            _register(user.id, base, base)
        return ws


# --- data rows + per-currency totals ---------------------------------------

def _read_data_rows(ws) -> list:
    """Existing expense rows (5 cells each), newest first, excluding the header
    and the per-currency total rows (totals have no Date)."""
    values = ws.get_values(value_render_option=ValueRenderOption.unformatted)
    rows = []
    for raw in values[1:]:
        cells = (list(raw) + [""] * len(HEADER))[: len(HEADER)]
        if str(cells[_DATE_COL]).strip():  # has a Date -> real expense row
            rows.append(cells)
    return rows


def _rewrite(ws, data_rows: list) -> None:
    """Rebuild the body: one total row per currency, then the data rows."""
    totals: dict = {}
    order: list = []
    for cells in data_rows:
        cur = str(cells[1]).strip()
        if cur not in totals:
            totals[cur] = 0.0
            order.append(cur)
        totals[cur] += _num(cells[0])
    currencies = sorted(order)
    total_rows = [[totals[c], c, "", "", ""] for c in currencies]
    body = total_rows + data_rows

    last_col = chr(ord("A") + len(HEADER) - 1)
    k = len(currencies)
    end = 1 + len(body)
    # RAW keeps amounts numeric and dates as plain text (no date/formula parsing).
    ws.update(body, f"A2:{last_col}{end}", value_input_option="RAW")
    ws.freeze(rows=1 + k)
    if k:
        ws.format(f"A2:{last_col}{1 + k}", {"textFormat": {"bold": True}})
    if end >= 2 + k:  # there is at least one data row
        ws.format(f"A{2 + k}:{last_col}{end}", {"textFormat": {"bold": False}})


# --- public API ------------------------------------------------------------

def append_expense(user: SheetUser, fields: dict, now: Optional[str] = None) -> None:
    currency = (fields.get("currency") or "").strip().upper() or "?"
    timestamp = now or datetime.now().strftime("%Y-%m-%d %H:%M")
    new_row = [
        _num(fields.get("amount", 0)),
        currency,
        fields.get("description", ""),
        timestamp,
        fields.get("category", ""),
    ]
    with _tab_lock:
        ws = _worksheet_for(user)
        data_rows = [new_row] + _read_data_rows(ws)  # newest on top
        _rewrite(ws, data_rows)


def read_all(user: SheetUser) -> list:
    """All of the user's expense rows (excludes the total rows)."""
    entry = _load_registry().get(str(user.id))
    if not entry or not entry.get("title"):
        return []
    try:
        ws = _spreadsheet().worksheet(entry["title"])
    except gspread.WorksheetNotFound:
        return []
    return [dict(zip(HEADER, cells)) for cells in _read_data_rows(ws)]
