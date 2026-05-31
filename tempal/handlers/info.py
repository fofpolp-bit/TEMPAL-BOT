"""Read-only info commands: /status /score /card."""

from __future__ import annotations

import io
import logging

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from ..game.models import GameStatus, LifetimeProfile, TeamId
from ..services.cards import render_card, render_card_text, render_profile, render_profile_text
from .common import BotContext, fmt_player_name, format_round_header, format_scoreboard, get_context

logger = logging.getLogger(__name__)
router = Router(name="info")


@router.message(Command("chatid"))
async def cmd_chatid(message: Message, **kwargs) -> None:
    """Reveal the current chat's Telegram ID — useful for whitelisting in env vars."""
    chat = message.chat
    kind = chat.type
    title = chat.title or chat.full_name or "(this chat)"
    await message.answer(
        f"<b>{title}</b>\n"
        f"тип: <code>{kind}</code>\n"
        f"ID: <code>{chat.id}</code>\n\n"
        f"Скопируй ID и пришли мне, чтоб я добавил его в whitelist.",
        parse_mode="HTML",
    )


@router.message(Command("status"))
async def cmd_status(message: Message, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    if not game:
        await message.answer("Здесь нет матча. /newgame — начать.")
        return
    if game.status is GameStatus.LOBBY:
        await message.answer(
            f"Лобби открыто. Игроков: {len(game.players)}. /startgame чтобы начать."
        )
        return
    if game.status is GameStatus.FINISHED:
        await message.answer("Матч уже завершён.")
        return
    await message.answer(format_round_header(game), parse_mode="HTML")


@router.message(Command("score"))
async def cmd_score(message: Message, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    if not game:
        await message.answer("Здесь нет матча.")
        return
    await message.answer(format_scoreboard(game), parse_mode="HTML")


@router.message(Command("profile"))
async def cmd_profile(message: Message, bot: Bot, **kwargs) -> None:
    """Out-of-game career profile: lifetime stats, wins, bronze achievements."""
    ctx: BotContext = get_context(kwargs)
    target_user = message.from_user
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user

    profile = ctx.profiles.get(target_user.id)
    if profile is None:
        profile = LifetimeProfile(
            user_id=target_user.id,
            name=target_user.full_name or "",
        )

    text = render_profile_text(profile)
    try:
        png = render_profile(profile)
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=BufferedInputFile(png, filename=f"profile_{target_user.id}.png"),
            caption=text,
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("render_profile failed for user %s", target_user.id)
        await message.answer(text, parse_mode="HTML")


@router.message(Command("card"))
async def cmd_card(message: Message, bot: Bot, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    # determine target player
    target_user = message.from_user
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user

    # find game in current chat OR any game with this user
    game = ctx.store.get(message.chat.id)
    if not game:
        for g in ctx.store.all_games():
            if target_user.id in g.players:
                game = g
                break
    if not game or target_user.id not in game.players:
        await message.answer("Этот человек не в матче.")
        return

    player = game.players[target_user.id]
    text = render_card_text(player)
    try:
        png = render_card(player)
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=BufferedInputFile(png, filename=f"card_{target_user.id}.png"),
            caption=text,
            parse_mode="HTML",
        )
    except Exception:
        await message.answer(text, parse_mode="HTML")
