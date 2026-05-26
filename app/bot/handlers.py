import logging
from typing import Any

from aiogram import F, Router, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from app.config.settings import settings
from app.services import brain, sheets, transcribe

router = Router()
logger = logging.getLogger(__name__)


class LogFlow(StatesGroup):
    confirming = State()


def format_confirmation(fields: dict) -> str:
    """Language-neutral confirmation card (emoji + values)."""
    return (
        "\U0001F4DD\n"
        f"\U0001F464 {fields.get('person') or '—'}\n"
        f"\U0001F3F7 {fields.get('category') or '—'}\n"
        f"\U0001F4B6 {fields.get('amount') or '—'} {fields.get('currency') or ''}\n"
        f"\U0001F4C4 {fields.get('description') or '—'}"
    )


def build_fields_for_sheet(parsed: dict, raw_text: str) -> dict:
    return {
        "person": parsed.get("person", ""),
        "category": parsed.get("category", ""),
        "amount": parsed.get("amount", ""),
        "currency": parsed.get("currency", ""),
        "description": parsed.get("description", ""),
        "raw_text": raw_text,
    }


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅", callback_data="confirm"),
                InlineKeyboardButton(text="❌", callback_data="cancel"),
            ]
        ]
    )


async def _process_text(message: types.Message, state: FSMContext, text: str) -> None:
    parsed = await brain.classify_and_extract(text)
    kind = parsed.get("type")

    if kind == "query":
        try:
            rows = sheets.read_all()
            answer = await brain.answer_query(text, rows)
        except Exception:
            logger.exception("query answering failed")
            await message.answer("⚠️ Something went wrong reading that, try again.")
            return
        await message.answer(answer)
        return

    if kind == "unclear":
        await message.answer("\U0001F914 " + (parsed.get("reason") or "?"))
        return

    # kind == "log"
    fields = build_fields_for_sheet(parsed, raw_text=text)
    await state.set_state(LogFlow.confirming)
    await state.update_data(pending=fields)
    await message.answer(format_confirmation(fields), reply_markup=_confirm_keyboard())


@router.message(CommandStart())
async def handle_start(message: types.Message) -> None:
    await message.answer("\U0001F44B Send an expense (text or voice), or ask about past expenses.")


@router.message(F.voice)
async def handle_voice(message: types.Message, state: FSMContext) -> None:
    if not settings.is_allowed(message.from_user.id):
        return
    try:
        file = await message.bot.get_file(message.voice.file_id)
        buf = await message.bot.download_file(file.file_path)
        text = await transcribe.transcribe_bytes(buf.read(), "voice.ogg")
    except Exception:
        logger.exception("voice download/transcription failed")
        await message.answer("⚠️ Couldn't read the voice note, please try again or type it.")
        return

    if not text:
        await message.answer("⚠️ Couldn't understand the voice note, please try again or type it.")
        return

    await _process_text(message, state, text)


@router.message(F.text)
async def handle_text(message: types.Message, state: FSMContext) -> None:
    if not settings.is_allowed(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text:
        return
    await _process_text(message, state, text)


@router.callback_query(F.data == "confirm")
async def cb_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    fields = data.get("pending")
    await state.set_state(None)
    if not fields:
        await callback.answer()
        return
    try:
        sheets.append_expense(fields)
    except Exception:
        logger.exception("sheet append failed")
        await callback.message.edit_text("⚠️ Couldn't save to the sheet — not logged. Try again.")
        await callback.answer()
        return
    await callback.message.edit_text("✅ " + format_confirmation(fields))
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    await state.update_data(pending=None)
    await callback.message.edit_text("❌")
    await callback.answer()
