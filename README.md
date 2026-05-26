# Expences Bot

Telegram bot that logs natural-language expenses (typed or voice) into a Google
Sheet after a confirmation tap, and answers questions about past expenses.
Runs as a Docker container (long polling), matching the other bots on the server.

## Configuration

Copy `.env.example` to `.env` and fill in:

- `telegram_bot_token` — from BotFather
- `claude_api_key` — reused Anthropic key
- `openai_api_key` — for Whisper voice transcription
- `google_sheet_id` — the spreadsheet's ID (from its URL)
- `google_creds_path` — path to the service-account JSON (default `service_account.json`)
- `allowed_user_ids` — leave empty during testing; fill with comma-separated
  Telegram user IDs before public deploy
- `port` — unused under Docker (the host/container port is set to `8006` in the
  `Dockerfile` and `docker-compose.yml`)

Also create a Google Cloud service account, download its JSON key as
`service_account.json` next to `docker-compose.yml`, and share the target sheet
with the service account's email (Editor access). Both `.env` and
`service_account.json` are gitignored and are provided on the server, not via git.

## Deploy (Docker, on the server)

```bash
git clone <repo-url> expences-bot
cd expences-bot

# Create the two secret files (not in git):
nano .env                       # paste the filled-in values
# upload service_account.json into this folder

docker compose up -d --build
```

The container exposes `/health` on host port **8006** (Telegram itself uses long
polling, so no inbound port is required for the bot to work).

### Update after pushing new code

```bash
cd expences-bot
git pull
docker compose up -d --build
```

### Logs / status

```bash
docker compose logs -f          # follow logs
docker compose ps               # container status
```

## Local development

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m pytest -v             # run the test suite
```
