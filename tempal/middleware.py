"""Aiogram middleware that injects the BotContext and enforces chat access."""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, TelegramObject

from .handlers.common import BotContext

logger = logging.getLogger(__name__)


class ContextMiddleware(BaseMiddleware):
    def __init__(self, ctx: BotContext) -> None:
        self._ctx = ctx

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["ctx"] = self._ctx
        return await handler(event, data)


class ChatAccessMiddleware(BaseMiddleware):
    """Whitelist-based access control.

    Rules:
      - Messages inside an allowed group chat → always allowed.
      - Messages from any other group chat → silently dropped.
      - DMs from a user who is a member of at least one allowed chat → allowed.
      - DMs from anyone else → bot replies with a polite refusal and drops the
        update.

    Callback queries inherit the same rules.

    A small TTL cache memoises `getChatMember` results to avoid spamming
    Telegram on every button press.
    """

    MEMBER_CACHE_TTL = 300  # seconds

    def __init__(
        self,
        allowed_chat_ids: frozenset[int],
        operate_chat_ids: frozenset[int],
        *,
        bot: Bot,
    ) -> None:
        self._allowed = allowed_chat_ids
        self._operate = operate_chat_ids
        self._bot = bot
        # cache key = (chat_id, user_id) → (is_member, expires_at)
        self._member_cache: dict[tuple[int, int], tuple[bool, float]] = {}

    BYPASS_COMMANDS = {"/chatid", "/start", "/help"}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id, user_id, message = self._extract_ids(event)

        # Allow a small set of utility commands through everywhere so the user
        # can fetch a chat ID before whitelisting it.
        if isinstance(event, Message) and event.text:
            head = event.text.split()[0].split("@")[0].lower()
            if head in self.BYPASS_COMMANDS:
                return await handler(event, data)

        # Inside a group chat: only chats explicitly listed in OPERATE_CHAT_IDS
        # are allowed to run games. Anything else is silently dropped so the
        # bot stays quiet in shared chats.
        if chat_id is not None and chat_id < 0:
            if not self._operate or chat_id in self._operate:
                return await handler(event, data)
            logger.debug(
                "dropping update from non-operate group %s", chat_id
            )
            return None

        # DM (chat_id positive, equals user_id) or any other context → check membership.
        if user_id is None:
            return await handler(event, data)

        is_member = await self._is_member_of_any_allowed(user_id)
        if is_member:
            return await handler(event, data)

        # Refuse politely once per DM.
        try:
            if isinstance(event, Message):
                await event.answer(
                    "⛔️ Доступ к этому боту есть только у участников игровых "
                    "чатов Темпала. Если ты должен(на) быть в списке — попроси "
                    "ведущего добавить тебя в чат.",
                )
            elif isinstance(event, CallbackQuery):
                await event.answer(
                    "Доступ только для участников игровых чатов Темпала.",
                    show_alert=True,
                )
        except TelegramAPIError:
            logger.exception("failed to reply with access-denied notice")
        return None

    def _extract_ids(self, event: TelegramObject):
        if isinstance(event, Message):
            return (
                event.chat.id if event.chat else None,
                event.from_user.id if event.from_user else None,
                event,
            )
        if isinstance(event, CallbackQuery):
            chat_id = event.message.chat.id if event.message and event.message.chat else None
            return (chat_id, event.from_user.id if event.from_user else None, None)
        return (None, None, None)

    async def _is_member_of_any_allowed(self, user_id: int) -> bool:
        now = time.time()
        for chat_id in self._allowed:
            cached = self._member_cache.get((chat_id, user_id))
            if cached and cached[1] > now:
                if cached[0]:
                    return True
                continue
            try:
                member = await self._bot.get_chat_member(chat_id, user_id)
                ok = member.status in {"creator", "administrator", "member", "restricted"}
            except TelegramAPIError as e:
                logger.warning("get_chat_member(%s, %s) failed: %s", chat_id, user_id, e)
                ok = False
            self._member_cache[(chat_id, user_id)] = (ok, now + self.MEMBER_CACHE_TTL)
            if ok:
                return True
        return False
