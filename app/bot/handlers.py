import asyncio
import logging
from typing import Optional

from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from app.config.settings import settings
from app.services import brain, sheets, transcribe
from app.services.sheets import SheetUser

router = Router()
logger = logging.getLogger(__name__)


class LogFlow(StatesGroup):
    clarifying = State()
    confirming = State()


def build_sheet_user(tg_user) -> SheetUser:
    return SheetUser(id=tg_user.id, first_name=tg_user.first_name, username=tg_user.username)


def format_confirmation(fields: dict) -> str:
    lines = []
    if fields.get("category"):
        lines.append(f"🏷 {fields['category']}")
    amount = fields.get("amount")
    currency = fields.get("currency") or ""
    lines.append(f"💶 {amount} {currency}".strip())
    lines.append(f"📄 {fields.get('description') or '—'}")
    return "\n".join(lines)


def build_fields_for_sheet(parsed: dict, raw_text: str) -> dict:
    return {
        "category": parsed.get("category", ""),
        "amount": parsed.get("amount", ""),
        "currency": parsed.get("currency", ""),
        "description": parsed.get("description", ""),
        "raw_text": raw_text,
    }


def missing_required(parsed: dict) -> list:
    missing = []
    amount = parsed.get("amount")
    if amount is None or amount == "":
        missing.append("amount")
    if not parsed.get("currency"):
        missing.append("currency")
    return missing


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅", callback_data="confirm"),
                InlineKeyboardButton(text="❌", callback_data="cancel"),
            ]
        ]
    )


def _ask_for_missing(missing: list) -> str:
    if "amount" in missing and "currency" in missing:
        return "💬 How much was it, and in what currency?"
    if "amount" in missing:
        return "💬 How much was it?"
    return "💬 What currency was that in?"


async def _safe_edit(callback: CallbackQuery, text: str) -> None:
    """Edit the callback's message if it is still accessible (None for stale callbacks)."""
    if callback.message is not None:
        try:
            await callback.message.edit_text(text)
        except Exception:
            logger.exception("edit_text failed")


async def _resolve_text(message: types.Message) -> Optional[str]:
    if message.voice:
        try:
            file = await message.bot.get_file(message.voice.file_id)
            buf = await message.bot.download_file(file.file_path)
            text = await transcribe.transcribe_bytes(buf.read(), "voice.ogg")
        except Exception:
            logger.exception("voice download/transcription failed")
            await message.answer("⚠️ Couldn't read the voice note, please try again or type it.")
            return None
        if not text:
            await message.answer("⚠️ Couldn't understand the voice note, please try again or type it.")
            return None
        return text
    text = (message.text or "").strip()
    return text or None


async def _present_log(message: types.Message, state: FSMContext, parsed: dict, raw_text: str) -> None:
    missing = missing_required(parsed)
    if missing:
        await state.set_state(LogFlow.clarifying)
        await state.update_data(accumulated=raw_text)
        await message.answer(_ask_for_missing(missing))
        return
    fields = build_fields_for_sheet(parsed, raw_text=raw_text)
    await state.set_state(LogFlow.confirming)
    await state.update_data(pending=fields)
    await message.answer(format_confirmation(fields), reply_markup=_confirm_keyboard())


async def _handle_expense_text(message: types.Message, state: FSMContext, text: str) -> None:
    parsed = await brain.classify_and_extract(text)
    kind = parsed.get("type")

    if kind == "query":
        try:
            rows = await asyncio.to_thread(sheets.read_all, build_sheet_user(message.from_user))
            answer = await brain.answer_query(text, rows)
        except Exception:
            logger.exception("query answering failed")
            await message.answer("⚠️ Something went wrong reading that, try again.")
            return
        await message.answer(answer)
        return

    if kind == "unclear":
        await message.answer("🤔 " + (parsed.get("reason") or "?"))
        return

    await _present_log(message, state, parsed, raw_text=text)


@router.message(CommandStart())
async def handle_start(message: types.Message, state: FSMContext) -> None:
    if not message.from_user or not settings.is_allowed(message.from_user.id):
        return
    await state.set_state(None)
    await message.answer("👋 Send an expense (text or voice), or ask about your expenses.")


@router.message(Command("cancel"))
async def handle_cancel(message: types.Message, state: FSMContext) -> None:
    await state.set_state(None)
    await state.update_data(pending=None, accumulated=None)
    await message.answer("❌ Cancelled.")


@router.message(LogFlow.clarifying)
async def handle_clarification(message: types.Message, state: FSMContext) -> None:
    if not message.from_user or not settings.is_allowed(message.from_user.id):
        return
    reply = await _resolve_text(message)
    if reply is None:
        return
    data = await state.get_data()
    accumulated = (data.get("accumulated") or "").strip()
    combined = f"{accumulated}. {reply}" if accumulated else reply
    parsed = await brain.classify_and_extract(combined)
    if parsed.get("type") != "log":
        await state.update_data(accumulated=combined)
        await message.answer(_ask_for_missing(["amount", "currency"]))
        return
    await _present_log(message, state, parsed, raw_text=combined)


@router.message(F.voice | F.text)
async def handle_input(message: types.Message, state: FSMContext) -> None:
    if not message.from_user or not settings.is_allowed(message.from_user.id):
        return
    text = await _resolve_text(message)
    if text is None:
        return
    await _handle_expense_text(message, state, text)


@router.callback_query(F.data == "confirm")
async def cb_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    fields = data.get("pending")
    await state.set_state(None)
    if not fields:
        await callback.answer()
        return
    try:
        await asyncio.to_thread(sheets.append_expense, build_sheet_user(callback.from_user), fields)
    except Exception:
        logger.exception("sheet append failed")
        await _safe_edit(callback, "⚠️ Couldn't save to the sheet — not logged. Try again.")
        await callback.answer()
        return
    await _safe_edit(callback, "✅\n" + format_confirmation(fields))
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    fields = data.get("pending")
    await state.set_state(None)
    await state.update_data(pending=None)
    if fields:
        await _safe_edit(callback, "❌\n" + format_confirmation(fields))
    else:
        await _safe_edit(callback, "❌")
    await callback.answer()
