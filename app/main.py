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

    await dp.stop_polling()
    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass
    await bot.session.close()


app = FastAPI(title="Expences Bot", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}
