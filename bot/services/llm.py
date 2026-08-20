"""
Клиент LLM: OpenAI (GPT-4o) или Anthropic (Claude).

Таймаут на запрос обязателен — иначе hang подвесит слот user-gate навсегда.
"""

from __future__ import annotations

import logging
from typing import Any

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from bot.config import Settings
from bot.persona import get_system_prompt

logger = logging.getLogger(__name__)

# Потолок длины одной реплики в контексте (защита RAM/токенов)
_MAX_HISTORY_MSG_CHARS = 4000


class LLMError(Exception):
    """Ошибка при обращении к провайдеру LLM."""


def normalize_history(
    history: list[dict[str, Any]],
    user_message: str,
) -> list[dict[str, str]]:
    """
    Собрать валидную цепочку сообщений для Chat API.

    - роли только user/assistant;
    - склеивает подряд идущие реплики одной роли;
    - убирает ведущие assistant;
    - режет слишком длинные куски;
    - текущее сообщение пользователя всегда в конце.
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

    user_message = user_message.strip()[:_MAX_HISTORY_MSG_CHARS]
    if not user_message:
        raise LLMError("Пустое сообщение пользователя")

    if merged and merged[-1]["role"] == "user":
        combined = f"{merged[-1]['content']}\n{user_message}"
        merged[-1]["content"] = combined[:_MAX_HISTORY_MSG_CHARS]
    else:
        merged.append({"role": "user", "content": user_message})

    return merged


class LLMService:
    """Единая точка вызова LLM с поддержкой двух провайдеров."""

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
        user_message: str,
    ) -> str:
        """Сгенерировать ответ ассистента."""
        messages = normalize_history(history, user_message)

        try:
            if self._settings.llm_provider == "openai":
                return await self._call_openai(messages)
            return await self._call_anthropic(messages)
        except LLMError:
            raise
        except Exception as exc:
            logger.exception("Неожиданная ошибка LLM")
            raise LLMError("Провайдер LLM временно недоступен") from exc

    async def _call_openai(self, messages: list[dict[str, str]]) -> str:
        if self._openai is None:
            raise LLMError("OpenAI-клиент не инициализирован")

        try:
            response = await self._openai.chat.completions.create(
                model=self._settings.openai_model,
                temperature=self._settings.llm_temperature,
                messages=[
                    {"role": "system", "content": get_system_prompt()},
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

    async def _call_anthropic(self, messages: list[dict[str, str]]) -> str:
        if self._anthropic is None:
            raise LLMError("Anthropic-клиент не инициализирован")

        try:
            response = await self._anthropic.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=4096,
                temperature=self._settings.llm_temperature,
                system=get_system_prompt(),
                messages=messages,
            )
        except Exception as exc:
            logger.exception("Ошибка Anthropic API")
            raise LLMError("Anthropic API вернул ошибку") from exc

        parts: list[str] = []
        for block in response.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)

        reply = "".join(parts).strip()
        if not reply:
            raise LLMError("Anthropic вернул пустой ответ")
        return reply

    async def close(self) -> None:
        """Закрыть HTTP-клиенты (идемпотентно)."""
        if self._openai is not None:
            await self._openai.close()
            self._openai = None
        if self._anthropic is not None:
            await self._anthropic.close()
            self._anthropic = None
