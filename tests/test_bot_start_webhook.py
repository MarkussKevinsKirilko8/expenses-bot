import pytest

from app.config.settings import settings
from app.services import bot_start_webhook as webhook


class _FakeUser:
    id = 555
    username = "mar"
    first_name = "Mario"
    last_name = "Rossi"


class _FakeResp:
    def __init__(self, status=200, text="ok"):
        self.status_code = status
        self.text = text


class _FakeClient:
    last = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeClient.last = {"url": url, "json": json, "headers": headers}
        return _FakeResp()


@pytest.mark.asyncio
async def test_notify_posts_expected_payload(monkeypatch):
    _FakeClient.last = None
    monkeypatch.setattr(settings, "bot_start_webhook_secret", "sek", raising=False)
    monkeypatch.setattr(webhook.httpx, "AsyncClient", _FakeClient)

    await webhook.notify_bot_start(_FakeUser())

    sent = _FakeClient.last
    assert sent["url"] == webhook.BOT_START_WEBHOOK_URL
    assert sent["headers"]["Authorization"] == "Bearer sek"
    assert sent["json"] == {
        "bot_name": "Expenses",
        "telegram_user_id": 555,
        "telegram_username": "mar",
        "telegram_first_name": "Mario",
        "telegram_last_name": "Rossi",
    }


@pytest.mark.asyncio
async def test_notify_skips_when_secret_empty(monkeypatch):
    _FakeClient.last = None
    monkeypatch.setattr(settings, "bot_start_webhook_secret", "", raising=False)
    monkeypatch.setattr(webhook.httpx, "AsyncClient", _FakeClient)

    await webhook.notify_bot_start(_FakeUser())
    assert _FakeClient.last is None  # no HTTP call made


@pytest.mark.asyncio
async def test_notify_swallows_errors(monkeypatch):
    monkeypatch.setattr(settings, "bot_start_webhook_secret", "sek", raising=False)

    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise RuntimeError("network down")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(webhook.httpx, "AsyncClient", _Boom)
    # Must not raise.
    await webhook.notify_bot_start(_FakeUser())
