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
from app.services.bot_start_webhook import schedule_bot_start_notification
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
    if fields.get("counterparty_name"):
        lines.append(f"👤 {fields['counterparty_name']}")
    if fields.get("category"):
        lines.append(f"🏷 {fields['category']}")
    amount = fields.get("amount")
    currency = fields.get("currency") or ""
    lines.append(f"💶 {amount} {currency}".strip())
    lines.append(f"📄 {fields.get('description') or '—'}")
    return "\n".join(lines)


def _capitalize(text: str) -> str:
    text = text or ""
    return text[:1].upper() + text[1:] if text else text


def build_fields_for_sheet(parsed: dict, raw_text: str) -> dict:
    return {
        "category": parsed.get("category", ""),
        "amount": parsed.get("amount", ""),
        "currency": parsed.get("currency", ""),
        "description": _capitalize(parsed.get("description", "")),
        "raw_text": raw_text,
    }


async def resolve_counterparty(parsed: dict, sender_id) -> tuple:
    """Return (counterparty_id, counterparty_name) when the named person is a
    known bot user (not the sender). (None, None) otherwise.

    First tries an exact match on the name/nicknames; if that finds nothing,
    asks Claude to resolve a possibly mistyped/mispronounced/transliterated name
    against the known users (it returns no match unless reasonably confident).
    Either way the user still confirms via the 👤 line + ✅ before any write."""
    name = (parsed.get("counterparty") or "").strip()
    if not name:
        return None, None

    exact = [
        uid for uid in sheets.find_user_ids_by_name(name) if str(uid) != str(sender_id)
    ]
    if len(exact) == 1:
        return exact[0], sheets.base_for(exact[0])
    if exact:  # genuinely ambiguous exact match -> don't guess
        return None, None

    candidates = [
        (uid, base, nicks)
        for (uid, base, nicks) in sheets.all_users()
        if str(uid) != str(sender_id)
    ]
    matched = await brain.match_person(name, candidates)
    if matched:
        return matched, sheets.base_for(matched)
    return None, None


def build_mirror_fields(fields: dict, mirror_description: str, sender_name: str) -> dict:
    """The counterparty's side of a linked transfer: amount flipped, and the
    description rephrased from their point of view ({me} -> the sender's name)."""
    try:
        amount = float(fields.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    mirror = dict(fields)
    mirror["amount"] = -amount
    if mirror_description:
        mirror["description"] = _capitalize(mirror_description.replace("{me}", sender_name or ""))
    return mirror


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
        return "💬 Сколько это было и в какой валюте?"
    if "amount" in missing:
        return "💬 Сколько это было?"
    return "💬 В какой валюте?"


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
            await message.answer("⚠️ Не удалось обработать голосовое сообщение. Попробуйте ещё раз или напишите текстом.")
            return None
        if not text:
            await message.answer("⚠️ Не удалось распознать голосовое сообщение. Попробуйте ещё раз или напишите текстом.")
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
    cp_id, cp_name = await resolve_counterparty(parsed, message.from_user.id)
    fields = build_fields_for_sheet(parsed, raw_text=raw_text)  # description already full
    pending = {
        "fields": fields,
        "counterparty_id": cp_id,
        "counterparty_name": cp_name,
        "mirror_description": parsed.get("mirror_description", "") or "",
    }
    await state.set_state(LogFlow.confirming)
    await state.update_data(pending=pending)
    card = dict(fields)
    if cp_name:
        card["counterparty_name"] = cp_name
    await message.answer(format_confirmation(card), reply_markup=_confirm_keyboard())


async def _handle_expense_text(message: types.Message, state: FSMContext, text: str) -> None:
    parsed = await brain.classify_and_extract(text)
    kind = parsed.get("type")

    if kind == "query":
        try:
            rows = await asyncio.to_thread(sheets.read_all, build_sheet_user(message.from_user))
            answer = await brain.answer_query(text, rows)
        except Exception:
            logger.exception("query answering failed")
            await message.answer("⚠️ Что-то пошло не так, попробуйте ещё раз.")
            return
        await message.answer(answer)
        return

    if kind == "unclear":
        await message.answer("🤔 " + (parsed.get("reason") or "Не понял, уточните, пожалуйста."))
        return

    await _present_log(message, state, parsed, raw_text=text)


START_TEXT = (
    "👋 Привет! Я бот для учёта расходов.\n\n"
    "Просто напишите или надиктуйте голосом трату, например:\n"
    "• «потратил 20 евро на обед»\n"
    "• «дал Марселю 100 евро за продукты»\n"
    "• «Марио дал мне 400 евро»\n\n"
    "Я покажу разобранную запись с кнопками ✅ / ❌ — нажмите ✅, и она попадёт "
    "в вашу таблицу. Если не указана сумма или валюта, я переспрошу.\n\n"
    "Чтобы узнать о тратах, просто спросите, например: "
    "«сколько я потратил в этом месяце?»"
)


@router.message(CommandStart())
async def handle_start(message: types.Message, state: FSMContext) -> None:
    if not message.from_user or not settings.is_allowed(message.from_user.id):
        return
    await state.set_state(None)
    # First-ever /start? (durable, lives in the Sheet) -> notify, in the background.
    is_new = await asyncio.to_thread(sheets.mark_user_started, message.from_user.id)
    await message.answer(START_TEXT)
    if is_new:
        schedule_bot_start_notification(message.from_user)


@router.message(Command("cancel"))
async def handle_cancel(message: types.Message, state: FSMContext) -> None:
    await state.set_state(None)
    await state.update_data(pending=None, accumulated=None)
    await message.answer("❌ Отменено.")


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


def _card(pending: dict) -> dict:
    card = dict(pending["fields"])
    if pending.get("counterparty_name"):
        card["counterparty_name"] = pending["counterparty_name"]
    return card


@router.callback_query(F.data == "confirm")
async def cb_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    pending = data.get("pending")
    await state.set_state(None)
    if not pending:
        await callback.answer()
        return
    fields = pending["fields"]
    cp_id = pending.get("counterparty_id")
    # 1) The user's own entry. If this fails, nothing was written — safe to retry.
    try:
        await asyncio.to_thread(sheets.append_expense, build_sheet_user(callback.from_user), fields)
    except Exception:
        logger.exception("sheet append failed")
        await _safe_edit(callback, "⚠️ Не удалось сохранить в таблицу — запись не добавлена. Попробуйте ещё раз.")
        await callback.answer()
        return
    # 2) The mirrored entry on the counterparty's sheet (best-effort, separate).
    if cp_id is not None:
        cp_user = sheets.user_for(cp_id)
        if cp_user is not None:
            sender_name = (
                sheets.base_for(callback.from_user.id)
                or callback.from_user.first_name
                or "me"
            )
            mirror = build_mirror_fields(fields, pending.get("mirror_description", ""), sender_name)
            try:
                await asyncio.to_thread(sheets.append_expense, cp_user, mirror)
            except Exception:
                logger.exception("mirror append failed")
                await _safe_edit(
                    callback,
                    "✅\n" + format_confirmation(_card(pending))
                    + f"\n⚠️ (сохранено у вас, но не удалось обновить таблицу {pending.get('counterparty_name')})",
                )
                await callback.answer()
                return
    await _safe_edit(callback, "✅\n" + format_confirmation(_card(pending)))
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    pending = data.get("pending")
    await state.set_state(None)
    await state.update_data(pending=None)
    if pending:
        await _safe_edit(callback, "❌\n" + format_confirmation(_card(pending)))
    else:
        await _safe_edit(callback, "❌")
    await callback.answer()
