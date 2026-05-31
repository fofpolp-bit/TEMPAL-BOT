"""Read-only info commands: /status /score /card."""

from __future__ import annotations

import io
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InputMediaPhoto, Message

from ..services.chronicle import build_match_chronicle

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


@router.callback_query(F.data == "end:chronicle")
async def cb_end_chronicle(call: CallbackQuery, **kwargs) -> None:
    """Re-post the chronicle text when the post-match button is pressed."""
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(call.message.chat.id)
    if not game or not game.chronicle:
        await call.answer("Хроника этого матча уже не доступна.", show_alert=True)
        return
    await call.message.answer(build_match_chronicle(game), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "end:cards")
async def cb_end_cards(call: CallbackQuery, bot: Bot, **kwargs) -> None:
    """Send PNG cards for every player who actually played the match."""
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(call.message.chat.id)
    if not game:
        await call.answer("Карточки этого матча уже не доступны.", show_alert=True)
        return
    eligible = [p for p in game.players.values() if p.is_eligible()]
    if not eligible:
        await call.answer("Нет игроков, которым можно показать карточки.", show_alert=True)
        return
    await call.answer()
    # Try sending up to 10 as a single album; fall back to one-by-one if a
    # card fails to render (we still want the rest to come through).
    media: list[InputMediaPhoto] = []
    fallbacks: list[tuple[str, str]] = []  # (caption, png-failure name)
    for p in eligible[:10]:
        try:
            png = render_card(p)
            media.append(
                InputMediaPhoto(
                    media=BufferedInputFile(png, filename=f"card_{p.user_id}.png"),
                    caption=render_card_text(p) if len(media) == 0 else None,
                    parse_mode="HTML" if len(media) == 0 else None,
                )
            )
        except Exception:
            logger.exception("render_card failed for player %s", p.user_id)
            fallbacks.append((render_card_text(p), p.name))
    if media:
        try:
            await bot.send_media_group(chat_id=call.message.chat.id, media=media)
        except Exception:
            logger.exception("send_media_group failed; falling back to text cards")
            for p in eligible:
                await call.message.answer(render_card_text(p), parse_mode="HTML")
            return
    for caption, _ in fallbacks:
        await call.message.answer(caption, parse_mode="HTML")
