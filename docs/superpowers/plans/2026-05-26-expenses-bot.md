# Expenses Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Telegram bot (aiogram, long-polling) that records natural-language expenses — typed or voice — into a Google Sheet after a confirmation tap, and answers questions about those expenses on demand.

**Architecture:** A FastAPI + uvicorn app whose lifespan runs aiogram long-polling as a background task and exposes `/health`. Incoming text/voice is transcribed (Whisper) if needed, classified+extracted by Claude (`log` / `query` / `unclear`), and either confirmed-then-appended to a Google Sheet or answered by reading the whole sheet back through Claude. Mirrors the conventions of the existing `orderhelpingbot`.

**Tech Stack:** Python 3.11+, aiogram 3.x, anthropic SDK, openai SDK (Whisper), gspread + google-auth, pydantic-settings, FastAPI + uvicorn, pytest + pytest-asyncio.

---

## File Structure

```
expences-bot/
  requirements.txt
  .env.example
  app/
    __init__.py
    main.py                  # FastAPI app; lifespan runs dp.start_polling; /health
    config/
      __init__.py
      settings.py            # pydantic-settings Settings
    bot/
      __init__.py
      setup.py               # Bot + Dispatcher (aiogram 3.x, MemoryStorage)
      handlers.py            # router: intake, allowlist, confirmation FSM, routing
    services/
      __init__.py
      transcribe.py          # OpenAI Whisper transcription
      brain.py               # Claude classify_and_extract / answer_query
      sheets.py              # gspread append_expense / read_all
  tests/
    __init__.py
    test_settings.py
    test_sheets.py
    test_brain.py
    test_transcribe.py
    test_handlers.py
```

Responsibility split mirrors `orderhelpingbot`: Telegram glue in `bot/`, external integrations in `services/`, config isolated in `config/`. Each service is testable without Telegram.

---

## Task 1: Project scaffolding & settings

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `app/__init__.py`, `app/config/__init__.py`, `app/bot/__init__.py`, `app/services/__init__.py`, `tests/__init__.py` (all empty)
- Create: `app/config/settings.py`
- Test: `tests/test_settings.py`

- [ ] **Step 1: Create `requirements.txt`**

```
aiogram==3.19.0
anthropic==0.42.0
openai==1.59.0
gspread==6.1.4
google-auth==2.37.0
pydantic-settings==2.7.1
fastapi==0.115.6
uvicorn==0.34.0
httpx==0.28.1

pytest==8.3.4
pytest-asyncio==0.25.0
```

- [ ] **Step 2: Create empty package files**

Create empty files: `app/__init__.py`, `app/config/__init__.py`, `app/bot/__init__.py`, `app/services/__init__.py`, `tests/__init__.py`.

- [ ] **Step 3: Create `.env.example`**

```
telegram_bot_token=
claude_api_key=
openai_api_key=
google_sheet_id=
google_creds_path=service_account.json
allowed_user_ids=
port=8081
```

- [ ] **Step 4: Write the failing test** in `tests/test_settings.py`

```python
import os

from app.config.settings import Settings


def test_allowed_user_ids_empty_means_allow_all():
    s = Settings(
        telegram_bot_token="t",
        claude_api_key="c",
        openai_api_key="o",
        google_sheet_id="sheet",
        allowed_user_ids="",
    )
    assert s.allowed_ids == set()
    assert s.is_allowed(12345) is True


def test_allowed_user_ids_parsed_and_enforced():
    s = Settings(
        telegram_bot_token="t",
        claude_api_key="c",
        openai_api_key="o",
        google_sheet_id="sheet",
        allowed_user_ids="111, 222",
    )
    assert s.allowed_ids == {111, 222}
    assert s.is_allowed(111) is True
    assert s.is_allowed(999) is False
```

- [ ] **Step 5: Run test to verify it fails**

Run: `cd /Users/markusskirilo/projects/expences-bot && python -m pytest tests/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `app.config.settings`.

- [ ] **Step 6: Write `app/config/settings.py`**

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    telegram_bot_token: str
    claude_api_key: str
    openai_api_key: str
    google_sheet_id: str
    google_creds_path: str = "service_account.json"
    # Comma-separated Telegram user IDs. Empty string = allow everyone (testing).
    allowed_user_ids: str = ""
    # uvicorn port for the /health endpoint; unique per bot on the server.
    port: int = 8081

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def allowed_ids(self) -> set[int]:
        return {
            int(part)
            for part in self.allowed_user_ids.split(",")
            if part.strip()
        }

    def is_allowed(self, user_id: int) -> bool:
        ids = self.allowed_ids
        return not ids or user_id in ids


settings = Settings()  # type: ignore[call-arg]
```

Note: `settings = Settings()` at import time requires env vars present. Tests
construct `Settings(...)` directly with kwargs, so they don't touch the
module-level singleton's env requirement — but importing the module does. To
keep tests hermetic, the test file sets dummy env vars via a `conftest.py`.

- [ ] **Step 7: Create `tests/conftest.py`** so importing `settings` never fails in tests

```python
import os

os.environ.setdefault("telegram_bot_token", "test-token")
os.environ.setdefault("claude_api_key", "test-claude")
os.environ.setdefault("openai_api_key", "test-openai")
os.environ.setdefault("google_sheet_id", "test-sheet")
os.environ.setdefault("allowed_user_ids", "")
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python -m pytest tests/test_settings.py -v`
Expected: PASS (both tests).

- [ ] **Step 9: Commit**

```bash
git add requirements.txt .env.example app/ tests/
git commit -m "feat: project scaffolding and settings"
```

---

## Task 2: Google Sheets service

**Files:**
- Create: `app/services/sheets.py`
- Test: `tests/test_sheets.py`

The sheet's first row is a fixed header. `append_expense` appends one row in
column order; `read_all` returns a list of dicts keyed by header.

- [ ] **Step 1: Write the failing test** in `tests/test_sheets.py`

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sheets.py -v`
Expected: FAIL with `ImportError`/`AttributeError` for `app.services.sheets`.

- [ ] **Step 3: Write `app/services/sheets.py`**

```python
from datetime import datetime
from functools import lru_cache
from typing import Any

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


def append_expense(fields: dict[str, Any], now: str | None = None) -> None:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sheets.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/sheets.py tests/test_sheets.py
git commit -m "feat: google sheets append/read service"
```

---

## Task 3: Claude brain service

**Files:**
- Create: `app/services/brain.py`
- Test: `tests/test_brain.py`

`classify_and_extract` returns a dict with `type` ∈ {`log`, `query`, `unclear`}.
`answer_query` returns a plain-language string. Both mirror the user's language.

- [ ] **Step 1: Write the failing test** in `tests/test_brain.py`

```python
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import brain


def _fake_response(text: str) -> MagicMock:
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


@pytest.mark.asyncio
async def test_classify_and_extract_returns_log(monkeypatch):
    payload = {
        "type": "log",
        "person": "Mario",
        "category": "groceries",
        "amount": 100,
        "currency": "EUR",
        "description": "weekly shop",
    }
    mock_create = AsyncMock(return_value=_fake_response(json.dumps(payload)))
    monkeypatch.setattr(brain._client.messages, "create", mock_create)

    result = await brain.classify_and_extract("Mario gave Marsels 100 euro for groceries")
    assert result["type"] == "log"
    assert result["amount"] == 100
    assert result["person"] == "Mario"


@pytest.mark.asyncio
async def test_classify_and_extract_returns_query(monkeypatch):
    mock_create = AsyncMock(return_value=_fake_response('{"type": "query"}'))
    monkeypatch.setattr(brain._client.messages, "create", mock_create)

    result = await brain.classify_and_extract("how much did Mario spend this month?")
    assert result["type"] == "query"


@pytest.mark.asyncio
async def test_classify_and_extract_invalid_json_returns_unclear(monkeypatch):
    mock_create = AsyncMock(return_value=_fake_response("not json at all"))
    monkeypatch.setattr(brain._client.messages, "create", mock_create)

    result = await brain.classify_and_extract("blah")
    assert result["type"] == "unclear"


@pytest.mark.asyncio
async def test_answer_query_returns_text(monkeypatch):
    mock_create = AsyncMock(return_value=_fake_response("Mario spent 340 EUR."))
    monkeypatch.setattr(brain._client.messages, "create", mock_create)

    answer = await brain.answer_query(
        "how much did Mario spend?",
        [{"Person": "Mario", "Amount": 340}],
    )
    assert answer == "Mario spent 340 EUR."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_brain.py -v`
Expected: FAIL with `ImportError` for `app.services.brain`.

- [ ] **Step 3: Write `app/services/brain.py`**

```python
import json
import logging
from typing import Any

from anthropic import AsyncAnthropic

from app.config.settings import settings

logger = logging.getLogger(__name__)

_client = AsyncAnthropic(api_key=settings.claude_api_key)

MODEL = "claude-opus-4-7"

CLASSIFY_SYSTEM = """You process messages for an expense-tracking bot. \
A message is EITHER a new expense to record, OR a question about past expenses.

Return ONLY a single JSON object, no prose, no markdown fences.

If the message records an expense, return:
{
  "type": "log",
  "person": "who the expense relates to (e.g. Mario), or empty string",
  "category": "short category you infer (food, fuel, materials, rent, ...) or empty string",
  "amount": <number, no currency symbol>,
  "currency": "ISO-ish code you infer (EUR, USD, ...). Default EUR if a euro amount with no explicit currency.",
  "description": "what it was for, in the message's own language"
}

If the message asks a question about past expenses (totals, who spent what, when), return:
{"type": "query"}

If it is an expense but you cannot find a clear amount, return:
{"type": "unclear", "reason": "short reason in the message's language"}

Always infer fields from the message's own language; do not translate names or descriptions."""

ANSWER_SYSTEM = """You answer questions about an expense ledger. \
You are given the user's question and the full ledger as JSON rows. \
Compute the answer (sums, filters by person/category/date as needed) and reply \
in plain language IN THE SAME LANGUAGE AS THE QUESTION. Be concise. \
If the data does not contain the answer, say so plainly."""


def _extract_text(response: Any) -> str:
    return "".join(b.text for b in response.content if getattr(b, "text", None)).strip()


async def classify_and_extract(text: str) -> dict[str, Any]:
    try:
        response = await _client.messages.create(
            model=MODEL,
            max_tokens=1000,
            system=CLASSIFY_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
        raw = _extract_text(response)
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Claude returned non-JSON for classify: %r", text)
        return {"type": "unclear", "reason": ""}
    except Exception:
        logger.exception("classify_and_extract failed")
        return {"type": "unclear", "reason": ""}

    if data.get("type") not in {"log", "query", "unclear"}:
        return {"type": "unclear", "reason": ""}
    return data


async def answer_query(question: str, rows: list[dict[str, Any]]) -> str:
    user_msg = (
        f"Question: {question}\n\n"
        f"Ledger rows (JSON):\n{json.dumps(rows, ensure_ascii=False)}"
    )
    response = await _client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=ANSWER_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    return _extract_text(response)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_brain.py -v`
Expected: PASS (all four tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/brain.py tests/test_brain.py
git commit -m "feat: claude classify/extract and query-answer service"
```

---

## Task 4: Voice transcription service

**Files:**
- Create: `app/services/transcribe.py`
- Test: `tests/test_transcribe.py`

Given raw audio bytes, send them to OpenAI Whisper and return the text. The
Telegram download itself happens in the handler (which owns the `Bot`); this
service only does the Whisper call so it is independently testable.

- [ ] **Step 1: Write the failing test** in `tests/test_transcribe.py`

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import transcribe


@pytest.mark.asyncio
async def test_transcribe_bytes_returns_text(monkeypatch):
    fake_result = MagicMock()
    fake_result.text = "Mario gave Marsels 100 euro"
    mock_create = AsyncMock(return_value=fake_result)
    monkeypatch.setattr(
        transcribe._client.audio.transcriptions, "create", mock_create
    )

    text = await transcribe.transcribe_bytes(b"fake-ogg-bytes", "voice.ogg")
    assert text == "Mario gave Marsels 100 euro"
    assert mock_create.await_count == 1


@pytest.mark.asyncio
async def test_transcribe_bytes_empty_result(monkeypatch):
    fake_result = MagicMock()
    fake_result.text = "   "
    monkeypatch.setattr(
        transcribe._client.audio.transcriptions,
        "create",
        AsyncMock(return_value=fake_result),
    )

    text = await transcribe.transcribe_bytes(b"x", "voice.ogg")
    assert text == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_transcribe.py -v`
Expected: FAIL with `ImportError` for `app.services.transcribe`.

- [ ] **Step 3: Write `app/services/transcribe.py`**

```python
import io
import logging

from openai import AsyncOpenAI

from app.config.settings import settings

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=settings.openai_api_key)

MODEL = "whisper-1"


async def transcribe_bytes(audio: bytes, filename: str = "voice.ogg") -> str:
    """Transcribe raw audio bytes via Whisper. Returns '' if nothing usable."""
    buffer = io.BytesIO(audio)
    buffer.name = filename  # OpenAI SDK uses the name to infer the format
    result = await _client.audio.transcriptions.create(
        model=MODEL,
        file=buffer,
    )
    return (result.text or "").strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_transcribe.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/transcribe.py tests/test_transcribe.py
git commit -m "feat: whisper voice transcription service"
```

---

## Task 5: Bot setup & handlers (intake, confirmation FSM, routing)

**Files:**
- Create: `app/bot/setup.py`
- Create: `app/bot/handlers.py`
- Test: `tests/test_handlers.py`

The handler logic that we unit-test is the *pure routing core* — extracted into
helper functions that take plain inputs and return decisions — so it can be
tested without a live Telegram server. The thin aiogram handlers call these
helpers.

- [ ] **Step 1: Write `app/bot/setup.py`** (no test; trivial wiring mirroring orderhelpingbot)

```python
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import router
from app.config.settings import settings

bot = Bot(
    token=settings.telegram_bot_token,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)
```

- [ ] **Step 2: Write the failing test** in `tests/test_handlers.py`

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_handlers.py -v`
Expected: FAIL with `ImportError`/`AttributeError` for `app.bot.handlers`.

- [ ] **Step 4: Write `app/bot/handlers.py`**

```python
import logging
from typing import Any

from aiogram import F, Router, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from app.config.settings import settings
from app.services import brain, sheets, transcribe

router = Router()
logger = logging.getLogger(__name__)


class LogFlow(StatesGroup):
    confirming = State()


def format_confirmation(fields: dict[str, Any]) -> str:
    """Language-neutral confirmation card (emoji + values)."""
    return (
        "📝\n"
        f"👤 {fields.get('person') or '—'}\n"
        f"🏷 {fields.get('category') or '—'}\n"
        f"💶 {fields.get('amount') or '—'} {fields.get('currency') or ''}\n"
        f"📄 {fields.get('description') or '—'}"
    )


def build_fields_for_sheet(parsed: dict[str, Any], raw_text: str) -> dict[str, Any]:
    return {
        "person": parsed.get("person", ""),
        "category": parsed.get("category", ""),
        "amount": parsed.get("amount", ""),
        "currency": parsed.get("currency", ""),
        "description": parsed.get("description", ""),
        "raw_text": raw_text,
    }


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅", callback_data="confirm"),
                InlineKeyboardButton(text="❌", callback_data="cancel"),
            ]
        ]
    )


async def _process_text(message: types.Message, state: FSMContext, text: str) -> None:
    parsed = await brain.classify_and_extract(text)
    kind = parsed.get("type")

    if kind == "query":
        try:
            rows = sheets.read_all()
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

    # kind == "log"
    fields = build_fields_for_sheet(parsed, raw_text=text)
    await state.set_state(LogFlow.confirming)
    await state.update_data(pending=fields)
    await message.answer(format_confirmation(fields), reply_markup=_confirm_keyboard())


@router.message(CommandStart())
async def handle_start(message: types.Message) -> None:
    await message.answer("👋 Send an expense (text or voice), or ask about past expenses.")


@router.message(F.voice)
async def handle_voice(message: types.Message, state: FSMContext) -> None:
    if not settings.is_allowed(message.from_user.id):
        return
    try:
        file = await message.bot.get_file(message.voice.file_id)
        buf = await message.bot.download_file(file.file_path)
        text = await transcribe.transcribe_bytes(buf.read(), "voice.ogg")
    except Exception:
        logger.exception("voice download/transcription failed")
        await message.answer("⚠️ Couldn't read the voice note, please try again or type it.")
        return

    if not text:
        await message.answer("⚠️ Couldn't understand the voice note, please try again or type it.")
        return

    await _process_text(message, state, text)


@router.message(F.text)
async def handle_text(message: types.Message, state: FSMContext) -> None:
    if not settings.is_allowed(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text:
        return
    await _process_text(message, state, text)


@router.callback_query(F.data == "confirm")
async def cb_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    fields = data.get("pending")
    await state.set_state(None)
    if not fields:
        await callback.answer()
        return
    try:
        sheets.append_expense(fields)
    except Exception:
        logger.exception("sheet append failed")
        await callback.message.edit_text("⚠️ Couldn't save to the sheet — not logged. Try again.")
        await callback.answer()
        return
    await callback.message.edit_text("✅ " + format_confirmation(fields))
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    await state.update_data(pending=None)
    await callback.message.edit_text("❌")
    await callback.answer()
```

Note on imports: `setup.py` imports `router` from `handlers`, and `handlers`
never imports `setup` (it uses `message.bot` at runtime). So there is no import
cycle: `main` → `setup` → `handlers` → `services`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_handlers.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add app/bot/setup.py app/bot/handlers.py tests/test_handlers.py
git commit -m "feat: bot handlers with confirmation FSM and routing"
```

---

## Task 6: FastAPI app entry point & deployment

**Files:**
- Create: `app/main.py`
- Create: `README.md` (run + deploy instructions)
- Create: `expences-bot.service` (systemd unit template)

- [ ] **Step 1: Write `app/main.py`** (mirrors `orderhelpingbot/app/main.py`)

```python
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bot.setup import bot, dp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    polling_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))

    yield

    dp.shutdown.set()
    polling_task.cancel()
    await bot.session.close()


app = FastAPI(title="Expences Bot", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 2: Verify the app imports and the bot wiring is sound (no live token needed)**

Run:
```bash
cd /Users/markusskirilo/projects/expences-bot && \
telegram_bot_token=x claude_api_key=x openai_api_key=x google_sheet_id=x \
python -c "import app.main; print('ok', [r.path for r in app.main.app.routes if hasattr(r, 'path')])"
```
Expected: prints `ok` and a list including `/health`. No exceptions.

- [ ] **Step 3: Run the full test suite**

Run: `python -m pytest -v`
Expected: ALL tests pass (settings, sheets, brain, transcribe, handlers).

- [ ] **Step 4: Write `README.md`**

```markdown
# Expences Bot

Telegram bot that logs natural-language expenses (typed or voice) into a Google
Sheet after a confirmation tap, and answers questions about past expenses.

## Setup

1. `python -m venv venv && source venv/bin/activate`
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in:
   - `telegram_bot_token` — from BotFather (new bot)
   - `claude_api_key` — reused Anthropic key
   - `openai_api_key` — for Whisper
   - `google_sheet_id` — the spreadsheet's ID (from its URL)
   - `google_creds_path` — path to the service-account JSON
   - `allowed_user_ids` — leave empty during testing; fill with comma-separated
     Telegram user IDs before public deploy
   - `port` — unique uvicorn port (next free after qualification_rt_bot)
4. Create a Google Cloud service account, download its JSON key to
   `google_creds_path`, and share the target sheet with the service account's
   email (Editor access).

## Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port "$port"
```

Telegram uses long polling; the port only serves `/health`.

## Test

```bash
python -m pytest -v
```
```

- [ ] **Step 5: Write `expences-bot.service`** (systemd template; adjust paths/port on the server)

```ini
[Unit]
Description=Expences Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/expences-bot
EnvironmentFile=/root/expences-bot/.env
ExecStart=/root/expences-bot/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8081
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 6: Commit**

```bash
git add app/main.py README.md expences-bot.service
git commit -m "feat: fastapi entry point, readme, systemd unit"
```

---

## Manual first-deploy checklist (run on the server after deploy)

- [ ] Confirm the chosen `port` is free (check DigitalOcean console / other bot units).
- [ ] `systemctl start expences-bot && systemctl status expences-bot` shows active.
- [ ] `curl localhost:<port>/health` returns `{"status":"ok"}`.
- [ ] Send a typed expense → confirmation card appears → tap ✅ → row in sheet.
- [ ] Send the same → tap ❌ → no row added.
- [ ] Send a voice note describing an expense → confirmation card appears → ✅ → row in sheet.
- [ ] Ask "how much did <person> spend this month?" → bot replies with a computed answer.
- [ ] Reply language matches the language you wrote/spoke in.

---

## Notes for the implementer

- **Models:** Claude `claude-opus-4-7`, Whisper `whisper-1`. Matches the existing bot's Claude usage.
- **Async everywhere:** aiogram 3.x, `AsyncAnthropic`, `AsyncOpenAI` are all async. gspread is sync — its calls are quick and run inside the handler; if it ever blocks noticeably, wrap with `asyncio.to_thread`. Out of scope for v1.
- **Prompt caching:** the classify system prompt is static and a candidate for Anthropic prompt caching later (see the claude-api skill); not required for v1.
- **Allowlist:** `settings.is_allowed()` returns `True` for everyone while `allowed_user_ids` is empty. Populate `.env` before public deploy — no code change needed.
```
