"""
Клиент LLM. История чата уже содержит текущую реплику.
"""

from __future__ import annotations

import logging
from typing import Any

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from bot.config import Settings
from bot.persona import get_proactive_prompt, get_reflect_prompt, get_system_prompt
from bot.services.emotions import (
    deflect_ai_accusation,
    looks_like_ai_admission,
    parse_reflect_json,
)

logger = logging.getLogger(__name__)

_MAX_HISTORY_MSG_CHARS = 4000


class LLMError(Exception):
    """Ошибка провайдера LLM."""


def normalize_chat_history(
    history: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """
    Подготовить ленту чата для API:
    merge соседних одинаковых ролей, срезать leading assistant, clip длины.
    """
    merged: list[dict[str, str]] = []

    for item in history:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        content = content[:_MAX_HISTORY_MSG_CHARS]
        if merged and merged[-1]["role"] == role:
            combined = f"{merged[-1]['content']}\n{content}"
            merged[-1]["content"] = combined[:_MAX_HISTORY_MSG_CHARS]
        else:
            merged.append({"role": role, "content": content})

    while merged and merged[0]["role"] != "user":
        merged.pop(0)

    if not merged:
        raise LLMError("Пустая история чата для ответа")
    if merged[-1]["role"] != "user":
        raise LLMError("Последнее сообщение в истории должно быть от собеседника")

    return merged


class LLMService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._openai: AsyncOpenAI | None = None
        self._anthropic: AsyncAnthropic | None = None
        timeout = settings.llm_timeout_sec

        if settings.llm_provider == "openai":
            self._openai = AsyncOpenAI(
                api_key=settings.openai_api_key,
                timeout=timeout,
                max_retries=2,
            )
        else:
            self._anthropic = AsyncAnthropic(
                api_key=settings.anthropic_api_key,
                timeout=timeout,
                max_retries=2,
            )

    async def generate_reply(
        self,
        history: list[dict[str, Any]],
        *,
        emotional_block: str = "",
    ) -> str:
        messages = normalize_chat_history(history)
        system = get_system_prompt(emotional_block=emotional_block)
        try:
            if self._settings.llm_provider == "openai":
                reply = await self._call_openai(messages, system=system)
            else:
                reply = await self._call_anthropic(messages, system=system)
        except LLMError:
            raise
        except Exception as exc:
            logger.exception("Неожиданная ошибка LLM")
            raise LLMError("Провайдер LLM временно недоступен") from exc

        if looks_like_ai_admission(reply):
            logger.warning("Анти-слив: ответ похож на признание ИИ — реген")
            harden = (
                system
                + "\n\nКРИТИЧНО: прошлый черновик почти спалил, что ты не человек. "
                "Перепиши ответ как живой Даня. ЗАПРЕЩЕНО любые намёки на ИИ/бота."
            )
            try:
                if self._settings.llm_provider == "openai":
                    reply = await self._call_openai(messages, system=harden)
                else:
                    reply = await self._call_anthropic(messages, system=harden)
            except LLMError:
                return deflect_ai_accusation()
            if looks_like_ai_admission(reply):
                return deflect_ai_accusation()
        return reply

    async def generate_proactive(
        self,
        *,
        user_name: str,
        messages: list[str],
        emotional_block: str = "",
    ) -> str:
        """Короткий живой коммент по теме последних смс пользователя."""
        if not messages:
            raise LLMError("Нет сообщений для проактивного анализа")
        bullet = "\n".join(f"- {m.strip()}" for m in messages if m and m.strip())
        if not bullet:
            raise LLMError("Пустые сообщения для проактивного анализа")

        user_prompt = (
            f"В групповом чате есть человек {user_name}. "
            f"Вот его последние сообщения:\n{bullet}\n\n"
            f"Напиши ОДНО короткое сообщение в чат по общей теме / вайбу этих смс. "
            f"Можно мягко обратиться к {user_name}, но без допроса и без «я проанализировал». "
            f"Как будто сам вспомнил тему и вкинул мысль. 1–4 предложения."
        )
        api_messages = [{"role": "user", "content": user_prompt}]
        system = get_proactive_prompt(emotional_block=emotional_block)
        try:
            if self._settings.llm_provider == "openai":
                reply = await self._call_openai(api_messages, system=system)
            else:
                reply = await self._call_anthropic(api_messages, system=system)
        except LLMError:
            raise
        except Exception as exc:
            logger.exception("Неожиданная ошибка LLM (proactive)")
            raise LLMError("Провайдер LLM временно недоступен") from exc

        if looks_like_ai_admission(reply):
            logger.warning("Анти-слив (proactive): реген")
            harden = (
                system
                + "\n\nКРИТИЧНО: черновик похож на признание ИИ. "
                "Перепиши как живой Даня. Без любых намёков на бота/ИИ."
            )
            try:
                if self._settings.llm_provider == "openai":
                    reply = await self._call_openai(api_messages, system=harden)
                else:
                    reply = await self._call_anthropic(
                        api_messages, system=harden
                    )
            except LLMError:
                raise LLMError("proactive: AI-leak после сбоя регена") from None
            if looks_like_ai_admission(reply):
                raise LLMError("proactive: AI-leak, слот не тратим на deflect")
        return reply

    async def reflect_emotions(
        self,
        *,
        history_tail: list[dict[str, Any]],
        my_reply: str,
    ) -> dict[str, Any] | None:
        """Обновить внутренний пульс после реплики (тихо, JSON)."""
        snippet_parts: list[str] = []
        for item in history_tail[-8:]:
            role = item.get("role")
            content = (item.get("content") or "").strip()[:400]
            if not content:
                continue
            who = "Я" if role == "assistant" else "Чат"
            snippet_parts.append(f"{who}: {content}")
        snippet_parts.append(f"Я (только что): {my_reply[:500]}")
        user_prompt = (
            "Обнови эмоциональное состояние Дани после этого куска переписки:\n"
            + "\n".join(snippet_parts)
        )
        try:
            if self._settings.llm_provider == "openai":
                raw = await self._call_openai(
                    [{"role": "user", "content": user_prompt}],
                    system=get_reflect_prompt(),
                    temperature=0.4,
                )
            else:
                raw = await self._call_anthropic(
                    [{"role": "user", "content": user_prompt}],
                    system=get_reflect_prompt(),
                    temperature=0.4,
                )
        except LLMError:
            logger.debug("reflect_emotions LLM fail", exc_info=True)
            return None
        return parse_reflect_json(raw)

    async def _call_openai(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float | None = None,
    ) -> str:
        if self._openai is None:
            raise LLMError("OpenAI-клиент не инициализирован")
        try:
            response = await self._openai.chat.completions.create(
                model=self._settings.openai_model,
                temperature=(
                    self._settings.llm_temperature
                    if temperature is None
                    else temperature
                ),
                messages=[
                    {"role": "system", "content": system or get_system_prompt()},
                    *messages,
                ],
            )
        except Exception as exc:
            logger.exception("Ошибка OpenAI API")
            raise LLMError("OpenAI API вернул ошибку") from exc

        choice = response.choices[0].message.content if response.choices else None
        if not choice or not choice.strip():
            raise LLMError("OpenAI вернул пустой ответ")
        return choice.strip()

    async def _call_anthropic(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float | None = None,
    ) -> str:
        if self._anthropic is None:
            raise LLMError("Anthropic-клиент не инициализирован")
        try:
            response = await self._anthropic.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=4096,
                temperature=(
                    self._settings.llm_temperature
                    if temperature is None
                    else temperature
                ),
                system=system or get_system_prompt(),
                messages=messages,
            )
        except Exception as exc:
            logger.exception("Ошибка Anthropic API")
            raise LLMError("Anthropic API вернул ошибку") from exc

        parts = [
            getattr(block, "text", "")
            for block in response.content
            if getattr(block, "text", None)
        ]
        reply = "".join(parts).strip()
        if not reply:
            raise LLMError("Anthropic вернул пустой ответ")
        return reply

    async def close(self) -> None:
        if self._openai is not None:
            await self._openai.close()
            self._openai = None
        if self._anthropic is not None:
            await self._anthropic.close()
            self._anthropic = None
