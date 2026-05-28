"""Cinematic dice rolling — animates a progress bar before revealing the result.

Telegram's bot API limits us to ~1 edit per second in groups (and bursts hit
"Flood control exceeded"). We use a short animation (3 frames) and gracefully
fall back to the reveal-only message if Telegram throttles us.
"""

from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from ..game.rolls import OUTCOME_LABEL, RollResult

logger = logging.getLogger(__name__)

BAR_FRAMES = [
    "🎲 ▱▱▱▱▱▱▱▱▱▱",
    "🎲 ▰▰▰▰▱▱▱▱▱▱",
    "🎲 ▰▰▰▰▰▰▰▰▱▱",
]


@dataclass
class CinematicRoll:
    chat_id: int
    message_id: int
    result: RollResult


async def animate_roll(
    bot: Bot,
    chat_id: int,
    *,
    title: str,
    result: RollResult,
    frame_delay: float = 0.6,
    reply_to_message_id: Optional[int] = None,
    text_prefix: str = "",
) -> CinematicRoll:
    """Send a short dice animation and reveal the result.

    Falls back to a single static message if Telegram rate-limits us.
    """

    safe_title = html.escape(title)
    prefix = (text_prefix + "\n") if text_prefix else ""
    initial = f"{prefix}<b>{safe_title}</b>\n{BAR_FRAMES[0]}"
    msg = await bot.send_message(
        chat_id, initial, parse_mode="HTML", reply_to_message_id=reply_to_message_id
    )

    rate_limited = False
    for frame in BAR_FRAMES[1:]:
        await asyncio.sleep(frame_delay)
        if rate_limited:
            break
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg.message_id,
                text=f"{prefix}<b>{safe_title}</b>\n{frame}",
                parse_mode="HTML",
            )
        except TelegramRetryAfter:
            rate_limited = True
            break
        except TelegramBadRequest:
            # message already at this text — ignore
            pass

    # final reveal
    reveal = _format_reveal(safe_title, result, prefix=prefix)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg.message_id,
            text=reveal,
            parse_mode="HTML",
        )
    except TelegramRetryAfter as exc:
        logger.warning("Flood control hit on reveal: retry in %ss — skipping edit", exc.retry_after)
    except TelegramBadRequest:
        pass
    return CinematicRoll(chat_id=chat_id, message_id=msg.message_id, result=result)


def _format_reveal(safe_title: str, result: RollResult, *, prefix: str = "") -> str:
    bar = "🎲 ▰▰▰▰▰▰▰▰▰▰"
    mod_str = ""
    if result.modifiers:
        mod_str = " (" + ", ".join(html.escape(label) for label, _ in result.modifiers) + ")"
    label = OUTCOME_LABEL[result.outcome]
    return (
        f"{prefix}<b>{safe_title}</b>\n"
        f"{bar} <b>{result.raw}</b>"
        + (f" → <b>{result.final}</b>{mod_str}" if result.modifiers else "")
        + f"\n{label}"
    )
