import json
import logging
from typing import Any

from anthropic import AsyncAnthropic

from app.config.settings import settings

logger = logging.getLogger(__name__)

_client = AsyncAnthropic(api_key=settings.claude_api_key)

MODEL = "claude-opus-4-7"

CLASSIFY_SYSTEM = """You process messages for an expense-tracking bot. \
A message is EITHER a new expense to record, OR a question about past expenses.

Return ONLY a single JSON object, no prose, no markdown fences.

If the message records an expense, return:
{
  "type": "log",
  "person": "who the expense relates to (e.g. Mario), or empty string",
  "category": "short category you infer (food, fuel, materials, rent, ...) or empty string",
  "amount": <number, no currency symbol>,
  "currency": "ISO-ish code you infer (EUR, USD, ...). Default EUR if a euro amount with no explicit currency.",
  "description": "what it was for, in the message's own language"
}

If the message asks a question about past expenses (totals, who spent what, when), return:
{"type": "query"}

If it is an expense but you cannot find a clear amount, return:
{"type": "unclear", "reason": "short reason in the message's language"}

Always infer fields from the message's own language; do not translate names or descriptions."""

ANSWER_SYSTEM = """You answer questions about an expense ledger. \
You are given the user's question and the full ledger as JSON rows. \
Compute the answer (sums, filters by person/category/date as needed) and reply \
in plain language IN THE SAME LANGUAGE AS THE QUESTION. Be concise. \
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
