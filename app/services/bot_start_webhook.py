import asyncio
import logging

import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)

# This bot's name in the notification service's allowed list (case-sensitive).
BOT_NAME = "Expenses"
BOT_START_WEBHOOK_URL = "https://app-notification.x8x.pro/functions/v1/bot-start-event"

# Strong references so fire-and-forget tasks aren't garbage-collected mid-flight.
_background_tasks: set = set()


async def notify_bot_start(user) -> None:
    """POST a 'new user joined' event. Fully best-effort: logs and swallows any
    error, and skips silently if the secret isn't configured."""
    secret = (settings.bot_start_webhook_secret or "").strip()
    if not secret:
        logger.info("BOT_START_WEBHOOK_SECRET not set; skipping new-user webhook")
        return
    payload = {
        "bot_name": BOT_NAME,
        "telegram_user_id": user.id,
        "telegram_username": user.username,
        "telegram_first_name": user.first_name,
        "telegram_last_name": user.last_name,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                BOT_START_WEBHOOK_URL,
                json=payload,
                headers={"Authorization": f"Bearer {secret}"},
            )
        if resp.status_code != 200:
            logger.warning(
                "bot-start webhook -> %s: %s", resp.status_code, resp.text[:200]
            )
    except Exception:
        logger.exception("bot-start webhook failed")


def schedule_bot_start_notification(user) -> None:
    """Fire-and-forget the webhook so the /start reply never waits on it."""
    task = asyncio.create_task(notify_bot_start(user))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
