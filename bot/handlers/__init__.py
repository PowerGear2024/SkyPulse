"""
Регистрация хендлеров Telethon.
"""

from __future__ import annotations

from telethon import TelegramClient
from telethon.tl.types import User

from bot.config import Settings
from bot.database import Database
from bot.handlers.messages import register_message_handlers
from bot.handlers.reactions import register_reaction_handlers
from bot.services.gate import ChatGate
from bot.services.llm import LLMService


def register_handlers(
    client: TelegramClient,
    *,
    db: Database,
    llm: LLMService,
    settings: Settings,
    gate: ChatGate,
    me: User,
) -> None:
    register_message_handlers(
        client, db=db, llm=llm, settings=settings, gate=gate, me=me
    )
    register_reaction_handlers(
        client, db=db, llm=llm, settings=settings, gate=gate, me=me
    )


__all__ = ["register_handlers"]
