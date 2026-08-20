"""
Регистрация хендлеров Telethon (lazy-import, без циклов).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telethon import TelegramClient
    from telethon.tl.types import User

    from bot.config import Settings
    from bot.database import Database
    from bot.services.gate import ChatGate
    from bot.services.llm import LLMService
    from bot.services.presence import OwnerGuard


def register_handlers(
    client: "TelegramClient",
    *,
    db: "Database",
    llm: "LLMService",
    settings: "Settings",
    gate: "ChatGate",
    guard: "OwnerGuard",
    me: "User",
) -> None:
    from bot.handlers.messages import register_message_handlers
    from bot.handlers.presence import register_presence_handlers
    from bot.handlers.reactions import register_reaction_handlers

    register_presence_handlers(
        client, db=db, settings=settings, guard=guard, me=me
    )
    register_message_handlers(
        client, db=db, llm=llm, settings=settings, gate=gate, guard=guard, me=me
    )
    register_reaction_handlers(
        client, db=db, llm=llm, settings=settings, gate=gate, guard=guard, me=me
    )


__all__ = ["register_handlers"]
