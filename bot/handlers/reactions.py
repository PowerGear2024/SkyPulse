"""
Ответ, когда кто-то ставит реакцию на НАШЕ сообщение в группе.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from telethon import TelegramClient, events, utils
from telethon.tl.types import (
    Channel,
    Chat,
    MessagePeerReaction,
    PeerChannel,
    PeerChat,
    PeerUser,
    ReactionCustomEmoji,
    ReactionEmoji,
    UpdateMessageReactions,
    User,
)

from bot.config import Settings
from bot.database import Database
from bot.handlers.helpers import as_telegram_user, display_name, ensure_user_from_sender
from bot.services.gate import ChatGate
from bot.services.llm import LLMService
from bot.services.presence import OwnerGuard
from bot.services.responder import generate_and_send

logger = logging.getLogger(__name__)

# Не отвечать дважды одному человеку на одну и ту же смс (TTL)
_DEDUP_TTL_SEC = 6 * 3600
_MAX_DEDUP = 2048


def register_reaction_handlers(
    client: TelegramClient,
    *,
    db: Database,
    llm: LLMService,
    settings: Settings,
    gate: ChatGate,
    guard: OwnerGuard,
    me: User,
) -> None:
    if not settings.reply_on_reactions:
        logger.info("REPLY_ON_REACTIONS выключен — реакции игнор")
        return

    seen: dict[tuple[int, int, int], float] = {}
    my_id = int(me.id)

    @client.on(events.Raw)
    async def on_raw(update: Any) -> None:
        if not isinstance(update, UpdateMessageReactions):
            return

        try:
            await _handle_reaction_update(
                client,
                update,
                db=db,
                llm=llm,
                settings=settings,
                gate=gate,
                guard=guard,
                me=me,
                my_id=my_id,
                seen=seen,
            )
        except Exception:
            logger.exception("Ошибка обработки реакции")


async def _handle_reaction_update(
    client: TelegramClient,
    update: UpdateMessageReactions,
    *,
    db: Database,
    llm: LLMService,
    settings: Settings,
    gate: ChatGate,
    guard: OwnerGuard,
    me: User,
    my_id: int,
    seen: dict[tuple[int, int, int], float],
) -> None:
    peer = update.peer
    # ЛС / каналы-вещалки не трогаем
    if isinstance(peer, PeerUser):
        return

    try:
        chat_id = int(utils.get_peer_id(peer))
    except Exception:
        logger.debug("Не удалось получить peer id из реакции", exc_info=True)
        return

    if not settings.is_chat_allowed(chat_id):
        return

    if not await _is_group_peer(client, peer, chat_id):
        return

    reactions = update.reactions
    if reactions is None:
        return

    recent = list(getattr(reactions, "recent_reactions", None) or [])
    if not recent:
        return

    if _total_reaction_count(reactions) <= 0:
        return

    # Берём самого свежего реактора, не нас
    reactor_peer = None
    reaction_obj = None
    for item in reversed(recent):
        if not isinstance(item, MessagePeerReaction):
            continue
        rid = _peer_user_id(item.peer_id)
        if rid is None or rid == my_id:
            continue
        reactor_peer = item.peer_id
        reaction_obj = item.reaction
        break

    if reactor_peer is None:
        return

    reactor_id = _peer_user_id(reactor_peer)
    if reactor_id is None:
        return

    if not settings.is_user_allowed(reactor_id):
        return

    blocked = guard.block_reason()
    if blocked:
        logger.debug("Реакция пропущена chat=%s: %s", chat_id, blocked)
        return

    msg_id = int(update.msg_id)
    key = (chat_id, msg_id, reactor_id)
    now = time.monotonic()
    _prune_seen(seen, now)
    if key in seen and now - seen[key] < _DEDUP_TTL_SEC:
        return

    # Сообщение должно быть нашим
    try:
        msg = await client.get_messages(chat_id, ids=msg_id)
    except Exception:
        logger.debug(
            "Не удалось загрузить сообщение chat=%s msg=%s",
            chat_id,
            msg_id,
            exc_info=True,
        )
        return

    if msg is None:
        return
    if not (getattr(msg, "out", False) or getattr(msg, "sender_id", None) == my_id):
        return

    sender = as_telegram_user(await client.get_entity(reactor_id))
    if sender is None:
        return

    await ensure_user_from_sender(db, sender)
    name = display_name(sender)
    emoji = _reaction_label(reaction_obj)
    snippet = (msg.message or "").strip()
    if len(snippet) > 180:
        snippet = snippet[:177] + "…"
    if not snippet:
        snippet = "(без текста)"

    seen[key] = now

    extra = [
        {
            "role": "user",
            "content": (
                f"[{name}]: *поставил(а) реакцию {emoji} "
                f"на моё сообщение «{snippet}» — ответь коротко и по-человечески*"
            ),
        }
    ]

    logger.info(
        "Реакция %s от %s на msg=%s в chat=%s",
        emoji,
        reactor_id,
        msg_id,
        chat_id,
    )

    await generate_and_send(
        client,
        db=db,
        llm=llm,
        settings=settings,
        gate=gate,
        guard=guard,
        chat_id=chat_id,
        my_id=my_id,
        history_extra=extra,
        reply_to=msg_id,
    )


def _peer_user_id(peer: Any) -> int | None:
    if isinstance(peer, PeerUser):
        return int(peer.user_id)
    return None


def _reaction_label(reaction: Any) -> str:
    if isinstance(reaction, ReactionEmoji):
        return reaction.emoticon or "👍"
    if isinstance(reaction, ReactionCustomEmoji):
        return "⭐"
    return "👍"


def _total_reaction_count(reactions: Any) -> int:
    results = getattr(reactions, "results", None) or []
    total = 0
    for r in results:
        total += int(getattr(r, "count", 0) or 0)
    return total


async def _is_group_peer(client: TelegramClient, peer: Any, chat_id: int) -> bool:
    if isinstance(peer, PeerChat):
        return True
    if isinstance(peer, PeerChannel):
        try:
            entity = await client.get_entity(chat_id)
        except Exception:
            logger.debug("get_entity для реакции не удался", exc_info=True)
            return False
        if isinstance(entity, Channel):
            return bool(getattr(entity, "megagroup", False))
        if isinstance(entity, Chat):
            return True
        return False
    return False


def _prune_seen(seen: dict[tuple[int, int, int], float], now: float) -> None:
    if len(seen) < _MAX_DEDUP:
        # Иногда чистим протухшие
        if len(seen) > 64 and len(seen) % 32 == 0:
            stale = [k for k, ts in seen.items() if now - ts > _DEDUP_TTL_SEC]
            for k in stale:
                seen.pop(k, None)
        return
    stale = [k for k, ts in seen.items() if now - ts > _DEDUP_TTL_SEC]
    for k in stale:
        seen.pop(k, None)
    while len(seen) >= _MAX_DEDUP:
        oldest = min(seen.items(), key=lambda kv: kv[1])[0]
        seen.pop(oldest, None)
