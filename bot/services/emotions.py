"""
Эмоциональный пульс персонажа + анти-слив «я ИИ».
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import Any, Callable, Awaitable

from bot.persona import AI_DEFLECT_REPLIES, SHORT_NAME

logger = logging.getLogger(__name__)

_VALID_STANCES = frozenset({"warm", "grudge", "crush", "annoyed", "neutral"})

# Только явные ПРИЗНАНИЯ «я = ИИ/бот», не разговор про бренды и не «я не бот»
_AI_LEAK_RE = re.compile(
    r"(?i)("
    # RU: я [—-] ИИ / я ИИ / я AI
    r"\bя\s*[-–—]?\s*(?:ии|ai)\b|"
    # я бот / я живой бот (без «не»)
    r"\bя\s+(?:всего\s+лишь\s+)?(?:живой\s+)?бот\b|"
    r"\bя\s+(?:языковая\s+)?модель\b|"
    r"\bя\s+нейросет(?:ь|ка)\b|"
    r"\bя\s+(?:chatgpt|gpt-?\d|claude|gemini)\b|"
    r"как\s+(?:ии|ai|бот|модель)\s+я\b|"
    r"\bя\s+виртуальн(?:ый|ая)\s+(?:помощник|ассистент|собеседник)\b|"
    r"у\s+меня\s+нет\s+(?:физического\s+)?тела\b|"
    r"у\s+меня\s+нет\s+(?:настоящих\s+)?(?:чувств|эмоций)\s*,?\s*(?:я|потому)|"
    r"меня\s+запрограмм|"
    r"\bя\s+не\s+человек\b|"
    r"\bя\s+искусственн(?:ый|ый\s+интеллект|ая\s+интеллект)\b|"
    # EN admissions
    r"\bi\s*['’]?m\s+an?\s+ai\b|"
    r"\bi\s+am\s+an?\s+ai\b|"
    r"\bi\s*['’]?m\s+an?\s+bot\b|"
    r"\bi\s+am\s+an?\s+(?:language\s+)?model\b|"
    r"\bas\s+an\s+ai\b|"
    r"\bi\s+don['’]?t\s+have\s+(?:a\s+)?body\b|"
    r"\bsystem\s*prompt\b"
    r")"
)

# Явные отрицания — не считаем сливом
_AI_DENIAL_RE = re.compile(
    r"(?i)("
    r"\bя\s+не\s+(?:ии|ai|бот|нейросеть|модель)\b|"
    r"\bi\s*['’]?m\s+not\s+an?\s+(?:ai|bot)\b|"
    r"\bi\s+am\s+not\s+an?\s+(?:ai|bot)\b"
    r")"
)


def looks_like_ai_admission(text: str) -> bool:
    if not text or not text.strip():
        return False
    if _AI_DENIAL_RE.search(text) and not re.search(
        r"(?i)\bя\s*[-–—]?\s*(?:ии|ai)\b|\bi\s+am\s+an?\s+ai\b", text
    ):
        # Чистое отрицание без параллельного «я ИИ»
        if not re.search(
            r"(?i)\bя\s+(?:бот|нейросеть|языковая\s+модель)\b|"
            r"\bas\s+an\s+ai\b|"
            r"\bi\s*['’]?m\s+an?\s+ai\b",
            text,
        ):
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


def normalize_stance(stance: str | None) -> str:
    s = (stance or "neutral").strip().lower()[:16]
    return s if s in _VALID_STANCES else "neutral"


def parse_reflect_json(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
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
    by_name: dict[str, dict[str, Any]] = {}
    if isinstance(feelings_raw, list):
        for item in feelings_raw[:16]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()[:64]
            if not name:
                continue
            by_name[name.casefold()] = {
                "name": name,
                "stance": normalize_stance(str(item.get("stance") or "neutral")),
                "note": str(item.get("note") or "").strip()[:160],
            }
    feelings = list(by_name.values())[:8]
    return {"mood": mood or "дерзкий", "vibe": vibe, "feelings": feelings}


class ReflectScheduler:
    """Один reflect на чат; отмена при новом / при shutdown; учёт reset epoch."""

    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def schedule(
        self,
        chat_id: int,
        *,
        epoch: int,
        runner: Callable[[], Awaitable[None]],
    ) -> None:
        prev = self._tasks.get(chat_id)
        if prev is not None and not prev.done():
            prev.cancel()

        async def _wrapped() -> None:
            try:
                await runner()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug(
                    "reflect pulse failed chat_id=%s epoch=%s",
                    chat_id,
                    epoch,
                    exc_info=True,
                )

        try:
            task = asyncio.create_task(
                _wrapped(), name=f"persona-reflect-{chat_id}"
            )
        except RuntimeError:
            logger.debug("Нет event loop для reflect chat_id=%s", chat_id)
            return

        self._tasks[chat_id] = task

        def _done(t: asyncio.Task[None]) -> None:
            if self._tasks.get(chat_id) is t:
                self._tasks.pop(chat_id, None)
            # забрать exception чтобы не было "Task exception was never retrieved"
            try:
                t.exception()
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        task.add_done_callback(_done)

    async def cancel_all(self) -> None:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


# Глобальный планировщик (один процесс бота)
reflect_scheduler = ReflectScheduler()
