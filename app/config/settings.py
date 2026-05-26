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
