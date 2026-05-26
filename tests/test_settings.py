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
