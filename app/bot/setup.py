from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import router
from app.config.settings import settings

bot = Bot(token=settings.telegram_bot_token)

dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)
