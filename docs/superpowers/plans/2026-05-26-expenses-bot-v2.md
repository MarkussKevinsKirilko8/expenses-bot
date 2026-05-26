# Expences Bot v2 (team feedback) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Apply the v2 team feedback: drop Person, signed amounts, no invented categories, required amount+currency with a clarification flow, confirmation history (✅/❌ keep details), and per-user tabs in the one spreadsheet.

**Architecture:** Same FastAPI + aiogram long-polling app. Three modules change: `sheets.py` (per-user worksheet routing + `_registry` tab), `brain.py` (new extraction schema), `handlers.py` (clarification FSM, /cancel, confirmation-history edits, per-user routing). Runtime: code must stay Python 3.9-compatible (local venv is 3.9.6; prod Docker is 3.12) — use `typing.Optional`, not `X | None`.

**Tech Stack:** Python (3.9-compatible), aiogram 3.x, anthropic, gspread, pytest + pytest-asyncio. Run tests with `./venv/bin/python -m pytest`.

---

## Task 1: Per-user Google Sheets routing (`sheets.py`)

**Files:**
- Rewrite: `app/services/sheets.py`
- Rewrite: `tests/test_sheets.py`

New columns: `["Date", "Category", "Amount", "Currency", "Description", "Raw text"]` (Person removed). Per-user tabs keyed by a `SheetUser(id, first_name, username)`. A `_registry` tab maps Telegram ID → tab title.

- [ ] **Step 1: Write the failing tests** in `tests/test_sheets.py`:

```python
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
    fake_ws.append_row.side_effect = lambda row, **kw: captured.setdefault("row", row)
    monkeypatch.setattr(sheets, "_worksheet_for", lambda user: fake_ws)

    user = SheetUser(id=111, first_name="Mario", username=None)
    sheets.append_expense(
        user,
        {
            "category": "groceries",
            "amount": -100,
            "currency": "EUR",
            "description": "weekly shop",
            "raw_text": "used 100 euro on groceries",
        },
        now="2026-05-26 14:00",
    )
    # Date, Category, Amount, Currency, Description, Raw text
    assert captured["row"] == [
        "2026-05-26 14:00",
        "groceries",
        -100,
        "EUR",
        "weekly shop",
        "used 100 euro on groceries",
    ]


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
    fake_ss.worksheets.return_value = []  # no existing tabs
    def _add_worksheet(title, rows, cols):
        created["title"] = title
        return fake_ws
    fake_ss.add_worksheet.side_effect = _add_worksheet

    monkeypatch.setattr(sheets, "_spreadsheet", lambda: fake_ss)
    monkeypatch.setattr(sheets, "_load_registry", lambda: {})
    registered = {}
    monkeypatch.setattr(sheets, "_register", lambda uid, title: registered.update({uid: title}))

    ws = sheets._worksheet_for(SheetUser(id=111, first_name="Mario", username="mar"))
    assert created["title"] == "Mario"
    assert registered == {111: "Mario"}
    fake_ws.update.assert_called_once()  # header written


def test_worksheet_for_name_collision_appends_id(monkeypatch):
    created = {}
    fake_ws = MagicMock()
    fake_ss = MagicMock()
    existing = MagicMock()
    existing.title = "Mario"
    fake_ss.worksheets.return_value = [existing]  # "Mario" already taken
    fake_ss.add_worksheet.side_effect = lambda title, rows, cols: created.setdefault("title", title) or fake_ws

    monkeypatch.setattr(sheets, "_spreadsheet", lambda: fake_ss)
    monkeypatch.setattr(sheets, "_load_registry", lambda: {})  # this id not registered
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
    fake_ss.add_worksheet.side_effect = lambda title, rows, cols: created.setdefault("title", title) or fake_ws
    monkeypatch.setattr(sheets, "_spreadsheet", lambda: fake_ss)
    monkeypatch.setattr(sheets, "_load_registry", lambda: {})
    monkeypatch.setattr(sheets, "_register", lambda uid, title: None)

    # no first name, has username
    sheets._worksheet_for(SheetUser(id=222, first_name=None, username="cooluser"))
    assert created["title"] == "cooluser"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_sheets.py -v`
Expected: failures/errors (SheetUser, _sanitize_title, _worksheet_for don't exist yet).

- [ ] **Step 3: Rewrite `app/services/sheets.py`**

```python
from collections import namedtuple
from datetime import datetime
from functools import lru_cache
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from app.config.settings import settings

# Per-user tab columns (Person removed in v2).
HEADER = ["Date", "Category", "Amount", "Currency", "Description", "Raw text"]

REGISTRY_TITLE = "_registry"
# Characters Google Sheets forbids in worksheet titles.
_INVALID_TITLE_CHARS = set(r"/\?*[]:")
_MAX_TITLE_LEN = 90

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Lightweight user identity passed in from the handlers (keeps aiogram types out
# of this module).
SheetUser = namedtuple("SheetUser", ["id", "first_name", "username"])

# In-memory cache of the registry: {str(telegram_id): tab_title}. Loaded lazily
# from the _registry tab (the durable source of truth across restarts).
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


def _worksheet_for(user: SheetUser):
    """Return the user's tab, creating (and registering) it on first contact."""
    reg = _load_registry()
    title = reg.get(str(user.id))
    ss = _spreadsheet()
    if title:
        try:
            return ss.worksheet(title)
        except gspread.WorksheetNotFound:
            pass  # registered tab was deleted; fall through and recreate

    desired = (
        _sanitize_title(user.first_name or "")
        or _sanitize_title(user.username or "")
        or str(user.id)
    )
    if not desired:
        desired = str(user.id)

    title = desired
    if title in _existing_titles():
        title = f"{desired} ({user.id})"

    ws = ss.add_worksheet(title=title, rows=1000, cols=len(HEADER))
    ws.update("A1", [HEADER])
    _register(user.id, title)
    return ws


def append_expense(user: SheetUser, fields: dict, now: Optional[str] = None) -> None:
    timestamp = now or datetime.now().strftime("%Y-%m-%d %H:%M")
    row = [
        timestamp,
        fields.get("category", ""),
        fields.get("amount", ""),
        fields.get("currency", ""),
        fields.get("description", ""),
        fields.get("raw_text", ""),
    ]
    _worksheet_for(user).append_row(row, value_input_option="USER_ENTERED")


def read_all(user: SheetUser) -> list:
    return _worksheet_for(user).get_all_records()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_sheets.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/sheets.py tests/test_sheets.py
git commit -m "feat: per-user worksheet routing with registry; drop Person column"
```

---

## Task 2: New extraction schema (`brain.py`)

**Files:**
- Modify: `app/services/brain.py`
- Modify: `tests/test_brain.py`

Drop Person. `amount` is signed (negative=out, positive=in) or `null` if not stated. `category` only if explicitly stated. `currency` only if stated (no default). Query/unclear unchanged.

- [ ] **Step 1: Update the tests** in `tests/test_brain.py` — replace the existing `test_classify_and_extract_returns_log` with the new-schema version, and add a signed-amount test. Keep the query / invalid-json / api-error / unknown-type tests as they are.

Replace the log test with:
```python
@pytest.mark.asyncio
async def test_classify_and_extract_returns_signed_log(monkeypatch):
    payload = {
        "type": "log",
        "category": "",
        "amount": -100,
        "currency": "EUR",
        "description": "new wallet",
    }
    mock_create = AsyncMock(return_value=_fake_response(json.dumps(payload)))
    monkeypatch.setattr(brain._client.messages, "create", mock_create)

    result = await brain.classify_and_extract("used 100 euro on a new wallet")
    assert result["type"] == "log"
    assert result["amount"] == -100
    assert result["currency"] == "EUR"
    assert result["category"] == ""
    assert "person" not in result


@pytest.mark.asyncio
async def test_classify_and_extract_positive_amount(monkeypatch):
    payload = {"type": "log", "category": "", "amount": 300, "currency": "EUR", "description": "from Marsels"}
    monkeypatch.setattr(
        brain._client.messages, "create",
        AsyncMock(return_value=_fake_response(json.dumps(payload))),
    )
    result = await brain.classify_and_extract("Marsels gave me 300 euro")
    assert result["amount"] == 300
```

- [ ] **Step 2: Run tests to verify the new ones fail / old assumptions removed**

Run: `./venv/bin/python -m pytest tests/test_brain.py -v`
Expected: the new tests pass only after the prompt/schema update; ensure no test still asserts a `person` field.

- [ ] **Step 3: Update `CLASSIFY_SYSTEM` and `ANSWER_SYSTEM` in `app/services/brain.py`.** Replace both prompt constants with:

```python
CLASSIFY_SYSTEM = """You process messages for a personal expense-tracking bot. \
A message is EITHER a new expense to record, OR a question about past expenses.

Return ONLY a single JSON object, no prose, no markdown fences.

If the message records an expense, return:
{
  "type": "log",
  "category": "<ONLY if the user explicitly names a category; otherwise empty string. NEVER invent or guess a category>",
  "amount": <a SIGNED number with NO currency symbol. NEGATIVE if money went OUT (spent, used, paid, bought), POSITIVE if money came IN (received, was given to the user). Use null if no amount is stated.>,
  "currency": "<currency code ONLY if the user states it (EUR, USD, GBP, ...); otherwise empty string. NEVER assume a currency.>",
  "description": "what it was about, in the message's own language"
}

Sign examples:
- "used 100 euro on a new wallet" -> amount: -100 (money went out)
- "spent 12.50 on lunch" -> amount: -12.5
- "Marsels gave me 300 euro" -> amount: 300 (money came in)
- "got paid 50" -> amount: 50

If the message asks a question about past expenses (totals, balances, what was spent/received, when), return:
{"type": "query"}

If you genuinely cannot tell whether it is an expense or a question, return:
{"type": "unclear", "reason": "short reason in the message's language"}

Infer fields from the message's own language; do not translate the description."""

ANSWER_SYSTEM = """You answer questions about a personal expense ledger. \
You are given the user's question and their ledger as JSON rows. \
Amounts are SIGNED: negative = money spent/out, positive = money received/in. \
Compute the answer (totals, net balance, filters by category/date as needed) and \
reply in plain language IN THE SAME LANGUAGE AS THE QUESTION. Be concise. \
If the data does not contain the answer, say so plainly."""
```

(The `classify_and_extract` and `answer_query` function bodies stay the same — only the prompt text changes. `classify_and_extract` already returns whatever keys Claude provides and validates `type`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_brain.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/brain.py tests/test_brain.py
git commit -m "feat: signed amounts, no invented category, no currency default"
```

---

## Task 3: Clarification flow, confirmation history, per-user routing (`handlers.py`)

**Files:**
- Rewrite: `app/services/`-aware `app/bot/handlers.py`
- Modify: `tests/test_handlers.py`

- [ ] **Step 1: Update the tests** in `tests/test_handlers.py` to the new helpers (no person, no 📝, plus `missing_required` and `build_sheet_user`):

```python
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
    assert "🏷" not in text  # no category line when empty
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_handlers.py -v`
Expected: failures (helpers changed / missing).

- [ ] **Step 3: Rewrite `app/bot/handlers.py`**:

```python
import asyncio
import logging
from typing import Optional

from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from app.config.settings import settings
from app.services import brain, sheets, transcribe
from app.services.sheets import SheetUser

router = Router()
logger = logging.getLogger(__name__)


class LogFlow(StatesGroup):
    clarifying = State()   # waiting for the user to supply a missing amount/currency
    confirming = State()   # waiting for the user to tap ✅ / ❌


def build_sheet_user(tg_user: types.User) -> SheetUser:
    return SheetUser(id=tg_user.id, first_name=tg_user.first_name, username=tg_user.username)


def format_confirmation(fields: dict) -> str:
    """Confirmation card (no leading symbol; ✅/❌ is prepended after the tap)."""
    lines = []
    if fields.get("category"):
        lines.append(f"🏷 {fields['category']}")
    amount = fields.get("amount")
    currency = fields.get("currency") or ""
    lines.append(f"💶 {amount} {currency}".strip())
    lines.append(f"📄 {fields.get('description') or '—'}")
    return "\n".join(lines)


def build_fields_for_sheet(parsed: dict, raw_text: str) -> dict:
    return {
        "category": parsed.get("category", ""),
        "amount": parsed.get("amount", ""),
        "currency": parsed.get("currency", ""),
        "description": parsed.get("description", ""),
        "raw_text": raw_text,
    }


def missing_required(parsed: dict) -> list:
    """Return which required fields (amount, currency) are missing from a log."""
    missing = []
    amount = parsed.get("amount")
    if amount is None or amount == "":
        missing.append("amount")
    if not parsed.get("currency"):
        missing.append("currency")
    return missing


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅", callback_data="confirm"),
                InlineKeyboardButton(text="❌", callback_data="cancel"),
            ]
        ]
    )


def _ask_for_missing(missing: list) -> str:
    if "amount" in missing and "currency" in missing:
        return "💬 How much was it, and in what currency?"
    if "amount" in missing:
        return "💬 How much was it?"
    return "💬 What currency was that in?"


async def _resolve_text(message: types.Message) -> Optional[str]:
    """Return the message's text, transcribing a voice note if needed. Sends a
    warning and returns None if the audio can't be understood."""
    if message.voice:
        try:
            file = await message.bot.get_file(message.voice.file_id)
            buf = await message.bot.download_file(file.file_path)
            text = await transcribe.transcribe_bytes(buf.read(), "voice.ogg")
        except Exception:
            logger.exception("voice download/transcription failed")
            await message.answer("⚠️ Couldn't read the voice note, please try again or type it.")
            return None
        if not text:
            await message.answer("⚠️ Couldn't understand the voice note, please try again or type it.")
            return None
        return text
    text = (message.text or "").strip()
    return text or None


async def _present_log(message: types.Message, state: FSMContext, parsed: dict, raw_text: str) -> None:
    """Either ask for missing amount/currency, or show the confirmation card."""
    missing = missing_required(parsed)
    if missing:
        await state.set_state(LogFlow.clarifying)
        await state.update_data(accumulated=raw_text)
        await message.answer(_ask_for_missing(missing))
        return
    fields = build_fields_for_sheet(parsed, raw_text=raw_text)
    await state.set_state(LogFlow.confirming)
    await state.update_data(pending=fields)
    await message.answer(format_confirmation(fields), reply_markup=_confirm_keyboard())


async def _handle_expense_text(message: types.Message, state: FSMContext, text: str) -> None:
    parsed = await brain.classify_and_extract(text)
    kind = parsed.get("type")

    if kind == "query":
        try:
            rows = await asyncio.to_thread(sheets.read_all, build_sheet_user(message.from_user))
            answer = await brain.answer_query(text, rows)
        except Exception:
            logger.exception("query answering failed")
            await message.answer("⚠️ Something went wrong reading that, try again.")
            return
        await message.answer(answer)
        return

    if kind == "unclear":
        await message.answer("🤔 " + (parsed.get("reason") or "?"))
        return

    await _present_log(message, state, parsed, raw_text=text)


@router.message(CommandStart())
async def handle_start(message: types.Message, state: FSMContext) -> None:
    if not message.from_user or not settings.is_allowed(message.from_user.id):
        return
    await state.set_state(None)
    # Create the user's tab eagerly so it exists from the first hello.
    try:
        await asyncio.to_thread(sheets._worksheet_for, build_sheet_user(message.from_user))
    except Exception:
        logger.exception("failed to provision user worksheet on /start")
    await message.answer("👋 Send an expense (text or voice), or ask about your expenses.")


@router.message(Command("cancel"))
async def handle_cancel(message: types.Message, state: FSMContext) -> None:
    await state.set_state(None)
    await state.update_data(pending=None, accumulated=None)
    await message.answer("❌ Cancelled.")


@router.message(LogFlow.clarifying)
async def handle_clarification(message: types.Message, state: FSMContext) -> None:
    if not message.from_user or not settings.is_allowed(message.from_user.id):
        return
    reply = await _resolve_text(message)
    if reply is None:
        return
    data = await state.get_data()
    accumulated = (data.get("accumulated") or "").strip()
    combined = f"{accumulated}. {reply}" if accumulated else reply
    parsed = await brain.classify_and_extract(combined)
    if parsed.get("type") != "log":
        # Treat the reply as more detail for the same expense; ask again.
        await state.update_data(accumulated=combined)
        await message.answer(_ask_for_missing(["amount", "currency"]))
        return
    await _present_log(message, state, parsed, raw_text=combined)


@router.message(F.voice | F.text)
async def handle_input(message: types.Message, state: FSMContext) -> None:
    if not message.from_user or not settings.is_allowed(message.from_user.id):
        return
    text = await _resolve_text(message)
    if text is None:
        return
    await _handle_expense_text(message, state, text)


@router.callback_query(F.data == "confirm")
async def cb_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    fields = data.get("pending")
    await state.set_state(None)
    if not fields:
        await callback.answer()
        return
    try:
        await asyncio.to_thread(sheets.append_expense, build_sheet_user(callback.from_user), fields)
    except Exception:
        logger.exception("sheet append failed")
        await callback.message.edit_text("⚠️ Couldn't save to the sheet — not logged. Try again.")
        await callback.answer()
        return
    await callback.message.edit_text("✅\n" + format_confirmation(fields))
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    fields = data.get("pending")
    await state.set_state(None)
    await state.update_data(pending=None)
    if fields:
        await callback.message.edit_text("❌\n" + format_confirmation(fields))
    else:
        await callback.message.edit_text("❌")
    await callback.answer()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_handlers.py -v`
Expected: the 4 helper tests pass.

- [ ] **Step 5: Run the FULL suite and verify clean import**

Run: `./venv/bin/python -m pytest -q` — expect all green.
Run import check:
`telegram_bot_token=123456:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw claude_api_key=x openai_api_key=x google_sheet_id=x ./venv/bin/python -c "import app.main; import app.bot.handlers; import app.bot.setup; print('ok')"`
Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add app/bot/handlers.py tests/test_handlers.py
git commit -m "feat: clarification flow, /cancel, confirmation history, per-user routing"
```

---

## Notes for the implementer
- Keep code Python 3.9-compatible: `typing.Optional`, not `X | None`. `dict`/`list`/`set` subscripts are fine.
- aiogram async handlers; gspread is sync so it is wrapped with `asyncio.to_thread` at call sites (already done in handlers).
- The `_registry` tab and per-user tabs all live in the single existing spreadsheet (`google_sheet_id`).
- Handler FSM-routed message handlers must be registered in this order so commands and the clarifying state win over the generic handler: `handle_start`, `handle_cancel`, `handle_clarification` (state-filtered), `handle_input` (generic). The code above is already in that order.
```
