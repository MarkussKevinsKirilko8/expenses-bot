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
