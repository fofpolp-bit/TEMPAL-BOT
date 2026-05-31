"""Outer session middleware that auto-retries Telegram flood-control errors.

Telegram occasionally returns ``TelegramRetryAfter`` when a chat or bot is
sending too many messages in a short window. Without this middleware,
``bot.send_message`` would simply raise and any handler that doesn't catch the
exception (notably the round bootstrap in :mod:`tempal.handlers.gameplay`)
would leave the game stuck mid-round.

We only retry *send* methods — edits are routinely best-effort throughout the
codebase (see ``services/dice.py``) and re-issuing them would block animations
and gameplay flow.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import (
    SendAnimation,
    SendDocument,
    SendMediaGroup,
    SendMessage,
    SendPhoto,
    SendVideo,
)
from aiogram.methods.base import Response, TelegramMethod, TelegramType

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

_RETRYABLE_METHODS: tuple[type, ...] = (
    SendMessage,
    SendPhoto,
    SendDocument,
    SendVideo,
    SendAnimation,
    SendMediaGroup,
)
_MAX_RETRY_SECONDS = 30.0


class RetryAfterMiddleware(BaseRequestMiddleware):
    """Retry send-style Telegram methods that hit flood control."""

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: "Bot",
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        if not isinstance(method, _RETRYABLE_METHODS):
            return await make_request(bot, method)
        attempts = 0
        while True:
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                attempts += 1
                delay = float(exc.retry_after) + 0.5
                if delay > _MAX_RETRY_SECONDS or attempts > 3:
                    logger.warning(
                        "Giving up on %s after %s flood retries (retry_after=%ss)",
                        type(method).__name__,
                        attempts,
                        exc.retry_after,
                    )
                    raise
                logger.info(
                    "Flood control on %s — sleeping %.1fs then retrying (attempt %d)",
                    type(method).__name__,
                    delay,
                    attempts,
                )
                await asyncio.sleep(delay)
