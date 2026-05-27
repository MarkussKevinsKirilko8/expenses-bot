import json
import logging
from typing import Any

from anthropic import AsyncAnthropic

from app.config.settings import settings

logger = logging.getLogger(__name__)

_client = AsyncAnthropic(api_key=settings.claude_api_key)

MODEL = "claude-opus-4-7"

CLASSIFY_SYSTEM = """You process messages for a personal expense-tracking bot. \
A message is EITHER a new expense to record, OR a question about past expenses.

Return ONLY a single JSON object, no prose, no markdown fences.

If the message records an expense, return:
{
  "type": "log",
  "category": "<ONLY if the user explicitly names a category; otherwise empty string. NEVER invent or guess a category>",
  "amount": <a SIGNED number with NO currency symbol. NEGATIVE if money went OUT (spent, used, paid, bought), POSITIVE if money came IN (received, was given to the user). Use null if no amount is stated.>,
  "currency": "<the 3-letter ISO currency CODE if the user names a currency, else empty string. Normalise words/symbols to the code: euro/euros/eur/€ -> EUR, dollar/dollars/usd/$ -> USD, pound/pounds/gbp/£ -> GBP, etc. Always upper-case. NEVER assume a currency if none is stated.>",
  "description": "<the PURPOSE only — what the money was FOR (e.g. 'groceries', 'lunch', 'new wallet'), in the message's own language. Do NOT include any person's name, and do NOT include words like 'gave', 'received', 'me', or the amount. Empty string if no purpose is stated.>",
  "counterparty": "<the OTHER person's name if the money was given to / received from a specific named person (e.g. 'I gave Marsels 100' -> 'Marsels'; 'Mario gave me 400' -> 'Mario'). Empty string if no other person is named. Do NOT put the speaker ('me','I') here.>",
  "action_note": "<used ONLY when a person is named AND no purpose was stated: a SHORT natural phrase in the message's own language describing the movement, e.g. 'gave Marsels money', 'money from Mario'. Empty string otherwise.>"
}

The amount sign is ALWAYS from the speaker's point of view: money the speaker hands out is negative, money the speaker receives is positive.

Examples (amount, counterparty, description, action_note):
- "used 100 euro on a new wallet" -> -100, "", "new wallet", ""
- "spent 12.50 on lunch" -> -12.5, "", "lunch", ""
- "I gave Marsels 100 euro for groceries" -> -100, "Marsels", "groceries", ""
- "i gave marsels 300 dollars" -> -300, "Marsels", "", "gave Marsels money"
- "Mario gave me 400 euro" -> 400, "Mario", "", "money from Mario"
- "got paid 50" -> 50, "", "", ""

If the message asks a question about past expenses (totals, balances, what was spent/received, when), return:
{"type": "query"}

If you genuinely cannot tell whether it is an expense or a question, return:
{"type": "unclear", "reason": "short reason in the message's language"}

Infer fields from the message's own language; do not translate the description."""

ANSWER_SYSTEM = """You answer questions about a personal expense ledger. \
You are given the user's question and their ledger as JSON rows. \
Amounts are SIGNED: negative = money spent/out, positive = money received/in. \
Compute the answer (totals, net balance, filters by category/date as needed) and \
reply in plain language IN THE SAME LANGUAGE AS THE QUESTION. Be concise. \
If the data does not contain the answer, say so plainly."""


def _extract_text(response: Any) -> str:
    return "".join(b.text for b in response.content if getattr(b, "text", None)).strip()


async def classify_and_extract(text: str) -> dict:
    try:
        response = await _client.messages.create(
            model=MODEL,
            max_tokens=1000,
            system=CLASSIFY_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
        raw = _extract_text(response)
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Claude returned non-JSON for classify: %r", text)
        return {"type": "unclear", "reason": ""}
    except Exception:
        logger.exception("classify_and_extract failed")
        return {"type": "unclear", "reason": ""}

    if data.get("type") not in {"log", "query", "unclear"}:
        return {"type": "unclear", "reason": ""}
    return data


async def answer_query(question: str, rows: list) -> str:
    user_msg = (
        f"Question: {question}\n\n"
        f"Ledger rows (JSON):\n{json.dumps(rows, ensure_ascii=False)}"
    )
    response = await _client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=ANSWER_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    return _extract_text(response)
