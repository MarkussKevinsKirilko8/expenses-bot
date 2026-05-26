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
