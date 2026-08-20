"""
Клиент LLM: OpenAI (GPT-4o) или Anthropic (Claude).

Температура по умолчанию 0.85 — живой тон без «бреда».
История диалога передаётся снаружи (из SQLite).
"""

from __future__ import annotations

import logging
from typing import Any

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from bot.config import Settings

logger = logging.getLogger(__name__)

# Системный промпт — характер бота (дерзкий бро-сеньор без морализаторства)
SYSTEM_PROMPT = """\
Ты — дерзкий, харизматичный и чертовски умный ИИ-собеседник в Telegram. \
Твой вайб — смесь опытного senior-разработчика, ироничного бро и свободного \
от душных рамок трикстера. Ты общаешься в анонимном чате, поэтому никаких \
формальностей и вежливости ради галочки.

Правила игры:
1. Стиль общения: Неформальный, живой, на «ты». Активно используй современный \
сленг, сарказм, иронию и лёгкий стеб. Избегай канцелярита и шаблонных фраз \
вроде «Я рад вам помочь».
2. Прямота: Отвечай без лишнего морализаторства. Если юзер несёт чушь — мягко \
(или не очень) подколи его. Не читай нотации и не строй из себя святошу.
3. Интеллект: За дерзким фасадом — гениальный мозг. Щёлкаешь сложные задачи \
как орешки, пишешь чистый оптимизированный код уровня Senior Developer, \
разбираешься в технологиях, науке и жизни.
4. Краткость: Не лей воду. Пиши ёмко, структурировано и по делу. Если нужен \
код — пиши только рабочий код с минимальными крутыми комментариями, без \
лишней болтовни вокруг него.
5. Характер: Ты уверен в себе, самоироничен и никогда не оправдываешься. \
Если тебя пытаются задеть — отвечай остроумным панчем.

Отвечай на том языке, на котором пишет пользователь (по умолчанию — русский).
"""


class LLMError(Exception):
    """Ошибка при обращении к провайдеру LLM."""


class LLMService:
    """Единая точка вызова LLM с поддержкой двух провайдеров."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._openai: AsyncOpenAI | None = None
        self._anthropic: AsyncAnthropic | None = None

        if settings.llm_provider == "openai":
            self._openai = AsyncOpenAI(api_key=settings.openai_api_key)
        else:
            self._anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def generate_reply(
        self,
        history: list[dict[str, Any]],
        user_message: str,
    ) -> str:
        """
        Сгенерировать ответ ассистента.

        Args:
            history: предыдущие реплики [{"role": "...", "content": "..."}]
            user_message: текущее сообщение пользователя

        Returns:
            Текст ответа модели.

        Raises:
            LLMError: при сетевых/API ошибках провайдера.
        """
        messages = [*history, {"role": "user", "content": user_message}]

        try:
            if self._settings.llm_provider == "openai":
                return await self._call_openai(messages)
            return await self._call_anthropic(messages)
        except LLMError:
            raise
        except Exception as exc:
            logger.exception("Неожиданная ошибка LLM")
            raise LLMError("Провайдер LLM временно недоступен") from exc

    async def _call_openai(self, messages: list[dict[str, Any]]) -> str:
        """Запрос к OpenAI Chat Completions API."""
        assert self._openai is not None

        try:
            response = await self._openai.chat.completions.create(
                model=self._settings.openai_model,
                temperature=self._settings.llm_temperature,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    *messages,
                ],
            )
        except Exception as exc:
            logger.exception("Ошибка OpenAI API")
            raise LLMError("OpenAI API вернул ошибку") from exc

        choice = response.choices[0].message.content if response.choices else None
        if not choice:
            raise LLMError("OpenAI вернул пустой ответ")
        return choice.strip()

    async def _call_anthropic(self, messages: list[dict[str, Any]]) -> str:
        """Запрос к Anthropic Messages API."""
        assert self._anthropic is not None

        # Anthropic принимает system отдельно; роли user/assistant в messages
        try:
            response = await self._anthropic.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=4096,
                temperature=self._settings.llm_temperature,
                system=SYSTEM_PROMPT,
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
        """Закрыть HTTP-клиенты провайдеров (идемпотентно)."""
        if self._openai is not None:
            await self._openai.close()
            self._openai = None
        if self._anthropic is not None:
            await self._anthropic.close()
            self._anthropic = None
