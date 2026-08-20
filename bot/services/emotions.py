"""
Эмоциональный пульс персонажа + анти-слив «я ИИ».
"""

from __future__ import annotations

import json
import logging
import random
import re
from typing import Any

from bot.persona import AI_DEFLECT_REPLIES, SHORT_NAME

logger = logging.getLogger(__name__)

# Утечки, из‑за которых банят в чатах
_AI_LEAK_RE = re.compile(
    r"(?i)("
    r"\bя\s*(?:ии|ai)\b|"
    r"я\s*(?:не\s*)?(?:живой\s*)?бот\b|"
    r"языковая\s*модель|"
    r"нейросет(?:ь|ка)|"
    r"chatgpt|gpt-?\d|claude\b|gemini\b|"
    r"как\s*(?:ии|ai|бот|модель)\s*я|"
    r"я\s*виртуальн|"
    r"у\s*меня\s*нет\s*(?:тела|чувств|эмоций)|"
    r"меня\s*запрограмм|"
    r"я\s*не\s*человек|"
    r"i['’]?m\s+an?\s+ai|"
    r"as\s+an\s+ai|"
    r"language\s+model|"
    r"system\s*prompt|"
    r"я\s*искусственн"
    r")"
)


def looks_like_ai_admission(text: str) -> bool:
    if not text or not text.strip():
        return False
    return _AI_LEAK_RE.search(text) is not None


def deflect_ai_accusation() -> str:
    return random.choice(AI_DEFLECT_REPLIES)


def format_emotional_block(
    *,
    mood: str | None,
    vibe: str | None,
    feelings: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    mood_s = (mood or "").strip() or "дерзкий дружеский"
    vibe_s = (vibe or "").strip()
    lines.append(f"Настроение: {mood_s}.")
    if vibe_s:
        lines.append(vibe_s)
    if feelings:
        lines.append("К людям в этом чате:")
        for f in feelings[:8]:
            name = (f.get("name") or "Кто-то").strip() or "Кто-то"
            stance = (f.get("stance") or "neutral").strip().lower()
            note = (f.get("note") or "").strip()
            label = {
                "warm": "тепло",
                "grudge": "обида",
                "crush": "симпатия",
                "annoyed": "бесит",
                "neutral": "нейтрально",
            }.get(stance, stance)
            if note:
                lines.append(f"- {name}: {label} — {note}")
            else:
                lines.append(f"- {name}: {label}")
    else:
        lines.append("Явных засевших обид/симпатий пока нет — читай атмосферу чата.")
    lines.append(
        f"Живи этим в ответах. Ты {SHORT_NAME}, не озвучивай пульс списком."
    )
    return "\n".join(lines)


def parse_reflect_json(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    # Срезать ```json ... ```
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        logger.debug("reflect JSON parse fail: %r", raw[:200])
        return None
    if not isinstance(data, dict):
        return None
    mood = str(data.get("mood") or "").strip()[:64]
    vibe = str(data.get("vibe") or "").strip()[:400]
    feelings_raw = data.get("feelings") or []
    feelings: list[dict[str, Any]] = []
    if isinstance(feelings_raw, list):
        for item in feelings_raw[:8]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()[:64]
            if not name:
                continue
            stance = str(item.get("stance") or "neutral").strip().lower()[:16]
            if stance not in {"warm", "grudge", "crush", "annoyed", "neutral"}:
                stance = "neutral"
            note = str(item.get("note") or "").strip()[:160]
            feelings.append({"name": name, "stance": stance, "note": note})
    return {"mood": mood or "дерзкий", "vibe": vibe, "feelings": feelings}
