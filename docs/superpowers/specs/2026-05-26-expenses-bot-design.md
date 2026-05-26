# Expenses Bot — Design Spec

**Date:** 2026-05-26
**Status:** Approved design, ready for implementation planning

## Summary

A Telegram bot that records expenses spoken or typed in natural language and
appends them to a shared Google Sheet, and also answers questions about those
expenses on demand ("how much did Mario spend this month?"). It reuses the
OpenAI Whisper (voice-to-text) and Anthropic Claude setup from the existing
bots, and deploys as a new service on the same DigitalOcean server.

Example input: *"Mario gave Marsels 100 euro for groceries"* → parsed, shown
back for confirmation, then written as a row in the sheet.

## Conventions (matched from `orderhelpingbot`)

The existing Python bots establish the house style, which this bot follows:

- **aiogram 3.x** for Telegram (router-based handlers, FSM with `MemoryStorage`)
- **pydantic-settings** `Settings` class loading from `.env`
- **FastAPI + uvicorn** app: Telegram long-polling runs as a background task in
  the FastAPI `lifespan`; a `/health` endpoint is served on the bot's own
  uvicorn port. This reconciles "long polling" with "a different port" — polling
  needs no inbound port, but uvicorn binds one for the health check, and each
  bot gets a unique port.
- **anthropic** SDK for Claude
- `app/` package split into `bot/`, `config/`, `services/`

## Architecture

```
app/
  main.py              # FastAPI app; lifespan runs dp.start_polling; /health endpoint
  config/settings.py   # pydantic-settings Settings (see Configuration below)
  bot/
    setup.py           # Bot + Dispatcher (aiogram 3.x, MemoryStorage for FSM)
    handlers.py        # router: text/voice handlers, confirmation buttons, routing
  services/
    transcribe.py      # OpenAI Whisper: download voice note -> text
    brain.py           # Claude: classify_and_extract(text), answer_query(question, rows)
    sheets.py          # gspread: append_expense(fields), read_all()
```

Each module has one responsibility and is testable in isolation.

### Module responsibilities

- **`config/settings.py`** — loads all secrets/config from `.env`.
- **`services/transcribe.py`** — given a Telegram voice file, downloads the
  `.ogg` and calls the OpenAI Whisper API, returns transcribed text.
- **`services/brain.py`** — the Claude layer.
  - `classify_and_extract(text)` → returns one of:
    - `{"type": "log", "person", "category", "amount", "currency", "description"}`
    - `{"type": "query"}`
    - `{"type": "unclear", "reason"}`
  - `answer_query(question, rows)` → takes the question plus all sheet rows and
    returns a plain-language answer.
- **`services/sheets.py`** — Google Sheets client via `gspread`.
  - `append_expense(fields)` → writes one row.
  - `read_all()` → returns every row for querying.
- **`bot/handlers.py`** — aiogram router. Intake, allowlist check, transcription,
  routing to log/query, and the confirmation FSM + inline keyboard.
- **`bot/setup.py`** — constructs `Bot` and `Dispatcher`.
- **`main.py`** — FastAPI app with lifespan running `dp.start_polling`; `/health`.

## Data model — Google Sheet columns

| Column      | Source                                    |
|-------------|-------------------------------------------|
| Date        | Auto-filled by the bot at write time      |
| Person      | Parsed by Claude (e.g. Mario)             |
| Category    | Guessed by Claude (food, fuel, materials) |
| Amount      | Parsed by Claude (numeric)                |
| Currency    | Parsed by Claude (EUR, USD)               |
| Description | Parsed by Claude (what it was for)        |
| Raw text    | The exact transcribed/typed message       |

## Data flow

### Logging an expense (text or voice)
1. Message arrives → `handlers.py` checks `allowed_user_ids`
   (empty = everyone passes, for testing).
2. Voice → `transcribe.py` downloads `.ogg` and Whisper returns text.
   Text → used as-is.
3. Text → `brain.classify_and_extract()` → returns a `log` entry.
4. Bot replies with the parsed fields + inline ✅ Confirm / ❌ Cancel.
   The pending entry is held in FSM state.
5. On **Confirm** → `sheets.append_expense()` writes
   `[date, person, category, amount, currency, description, raw_text]`,
   reply "✅ Logged." On **Cancel** → discard, reply "❌ Discarded."

### Answering a query
1. Same intake (steps 1–3); Claude returns `{"type": "query"}`.
2. `sheets.read_all()` pulls every row.
3. `brain.answer_query(question, rows)` → Claude answers in plain language
   (e.g. "Mario spent 340 EUR this month across 5 entries").
4. Bot sends the answer directly — no confirmation for reads.

### Ambiguity
If Claude cannot extract a clear amount, it returns `{"type": "unclear", reason}`
and the bot asks the user to rephrase rather than logging garbage.

## Configuration (`.env`)

| Key                  | Purpose                                              |
|----------------------|------------------------------------------------------|
| `telegram_bot_token` | The new bot's token from BotFather                   |
| `claude_api_key`     | Reused Anthropic key                                 |
| `openai_api_key`     | For Whisper transcription                            |
| `google_sheet_id`    | Target spreadsheet ID                                |
| `google_creds_path`  | Path to the service-account JSON                     |
| `allowed_user_ids`   | Comma-separated Telegram IDs; empty = allow all      |
| `port`               | uvicorn port for `/health` (unique per bot)          |

## Google Sheets auth

Service account: create a Google Cloud service account, download its JSON key,
share the target sheet with the service account's email (Editor). The bot
authenticates via `google-auth` and accesses the sheet via `gspread`. No login
expiry, no OAuth flow.

## Access control

For testing the bot is open to the team (`allowed_user_ids` empty). The
allowlist guardrail is built in from day one: when populated, only listed
Telegram user IDs are served; others are ignored. Enabling the guardrail before
deploy is a config change only, no code change.

## Error handling

Every failure replies to the user and never crashes the bot:

- **Whisper fails / empty audio** → "Couldn't understand the voice note, please
  try again or type it."
- **Claude error or invalid JSON** → "Something went wrong reading that, try
  again." (logged server-side)
- **Sheets write fails** → "Couldn't save to the sheet — not logged. Try again."
- **Unauthorized user** (allowlist enabled) → ignored / short "Not authorized."
- All exceptions logged via the standard `logging` setup (matches
  `orderhelpingbot`).

## Testing

- **`brain.py`** — unit tests with sample sentences (Latvian/English, messy
  voice-style text) asserting classification and extracted fields. Claude calls
  mocked for deterministic logic; a few real-call smoke tests.
- **`sheets.py`** — tested against a throwaway test sheet: append a row, read it
  back.
- **`transcribe.py`** — mocked OpenAI client; assert audio-download → API path.
- **`handlers.py`** — confirmation FSM flow tested with aiogram test utilities
  (mock Confirm/Cancel callbacks).
- **Manual first-deploy checklist:** send a typed expense, a voice expense,
  hit Confirm, hit Cancel, ask a query.

## Dependencies

On top of what `orderhelpingbot` already uses (`aiogram`, `anthropic`,
`fastapi`, `uvicorn`, `pydantic-settings`, `httpx`):

- `openai` — Whisper transcription
- `gspread` + `google-auth` — Google Sheets

## Deployment

New systemd service on the existing DigitalOcean server, on a unique uvicorn
port (next free port after `qualification_rt_bot` — confirm in the DO console).
Long polling means no webhook, reverse proxy, or SSL is required for Telegram;
the port only serves `/health`.

## Out of scope (for now)

- Pre-filtering/aggregating sheet rows in Python before querying — full-sheet
  read into Claude context is sufficient at expense-book scale.
- Editing/deleting existing entries via the bot.
- Per-person balances or running totals computed automatically.
- Multi-sheet / multi-currency conversion.
