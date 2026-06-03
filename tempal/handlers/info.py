"""Read-only info commands: /status /score /card."""

from __future__ import annotations

import html
import io
import logging
import random

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InputMediaPhoto, Message

from ..services.chronicle import build_match_chronicle

from ..game.achievements import ACHIEVEMENTS, get_achievement
from ..game.models import GameStatus, LifetimeProfile, TEAM_LABEL, TeamId
from ..game.zones import get_zone
from ..services.cards import render_card, render_card_text, render_profile, render_profile_text
from .common import BotContext, fmt_player_name, format_round_header, format_scoreboard, get_context
from .keyboards import end_game_keyboard

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


def _resolve_finished_game(ctx: BotContext, chat_id: int):
    """Pick the relevant finished game for post-match buttons / /history.

    Prefer the *live* store if the current match is already FINISHED
    (immediately after auto-finish or /endgame, before /newgame replaces
    it). Otherwise fall back to the per-chat snapshot of the last
    finished match.
    """
    game = ctx.store.get(chat_id)
    if game and game.status is GameStatus.FINISHED and game.chronicle:
        return game
    return ctx.last_match.get(chat_id)


@router.callback_query(F.data == "end:chronicle")
async def cb_end_chronicle(call: CallbackQuery, **kwargs) -> None:
    """Re-post the chronicle text when the post-match button is pressed."""
    ctx: BotContext = get_context(kwargs)
    game = _resolve_finished_game(ctx, call.message.chat.id)
    if not game or not game.chronicle:
        await call.answer("Хроника этого матча уже не доступна.", show_alert=True)
        return
    await call.message.answer(build_match_chronicle(game), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "end:cards")
async def cb_end_cards(call: CallbackQuery, bot: Bot, **kwargs) -> None:
    """Send PNG cards for every player who actually played the match."""
    ctx: BotContext = get_context(kwargs)
    game = _resolve_finished_game(ctx, call.message.chat.id)
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


# ── /top, /history, /zone, /coinflip ────────────────────────────────


_TOP_MODES = {
    "wins": (
        "побед",
        lambda p: (p.matches_won, p.matches_played, p.total_personal_score),
    ),
    "score": (
        "очков",
        lambda p: (p.total_personal_score, p.matches_won, p.matches_played),
    ),
    "bronzes": (
        "ачивок",
        lambda p: (sum(p.achievement_counts.values()), p.matches_won, p.matches_played),
    ),
}


def _profile_value(profile: LifetimeProfile, mode: str) -> int:
    if mode == "wins":
        return profile.matches_won
    if mode == "score":
        return profile.total_personal_score
    if mode == "bronzes":
        return sum(profile.achievement_counts.values())
    return 0


@router.message(Command("top"))
async def cmd_top(message: Message, **kwargs) -> None:
    """Top-10 chat leaderboard. /top [wins|score|bronzes]."""
    ctx: BotContext = get_context(kwargs)
    parts = (message.text or "").split()
    mode = parts[1].lower() if len(parts) > 1 else "wins"
    if mode not in _TOP_MODES:
        await message.answer("Используй /top, /top score или /top bronzes.")
        return

    label, _ = _TOP_MODES[mode]
    sort_key = _TOP_MODES[mode][1]
    chat_id = message.chat.id

    candidates: list[LifetimeProfile] = []
    for profile in ctx.profiles.all_profiles():
        if profile.matches_played <= 0:
            continue
        # Empty chat_ids = legacy profile from before this field existed;
        # show those too so old stats don't disappear.
        if profile.chat_ids and chat_id not in profile.chat_ids:
            continue
        candidates.append(profile)

    if not candidates:
        await message.answer(
            "Топа пока нет — никто в этом чате ещё не доигрывал матч до конца."
        )
        return

    candidates.sort(key=sort_key, reverse=True)
    top = candidates[:10]
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🏆 <b>Топ-{len(top)} по {label}</b> этого чата:"]
    for idx, p in enumerate(top):
        medal = medals[idx] if idx < 3 else f"{idx + 1}."
        value = _profile_value(p, mode)
        name = fmt_player_name(p.name or f"Игрок #{p.user_id}")
        extra = (
            f"({p.matches_won}/{p.matches_played})"
            if mode != "wins"
            else f"({p.matches_played} матчей)"
        )
        lines.append(f"{medal} <b>{name}</b> — {value} {extra}")
    lines.append("")
    lines.append(
        "<i>Режимы: /top, /top score, /top bronzes.</i>"
    )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("history"))
async def cmd_history(message: Message, **kwargs) -> None:
    """Show the chronicle of the last finished match in this chat."""
    ctx: BotContext = get_context(kwargs)
    game = ctx.last_match.get(message.chat.id)
    if not game or not game.chronicle:
        await message.answer(
            "В этом чате ещё не было завершённых матчей. Сыграйте один — /newgame."
        )
        return
    await message.answer(
        build_match_chronicle(game),
        parse_mode="HTML",
        reply_markup=end_game_keyboard(),
    )


@router.message(Command("zone"))
async def cmd_zone(message: Message, **kwargs) -> None:
    """Describe the current zone (only meaningful during a live match)."""
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    if not game or game.status is not GameStatus.PLAYING:
        await message.answer("Зона показывается только во время активного матча.")
        return
    zone = get_zone(game.current_zone_id)
    lines = [
        f"{zone.emoji} <b>{zone.name}</b>",
        f"<i>{zone.description}</i>",
    ]
    if zone.roll_bonus:
        sign = "+" if zone.roll_bonus > 0 else ""
        lines.append(f"Модификатор к роллам: <b>{sign}{zone.roll_bonus}</b>")
    if zone.inverts_rolls:
        lines.append("Все роллы инвертированы (20 ↔ 1).")
    if game.chaos:
        lines.append("")
        lines.append(
            f"{game.chaos.effect_emoji} <b>Хаос текущей минуты:</b> "
            f"<i>{game.chaos.effect_label}</i>"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("coinflip"))
async def cmd_coinflip(message: Message, **kwargs) -> None:
    """Random coin flip: which team gets the first move / honor of going first."""
    team = random.choice([TeamId.A, TeamId.B])
    label = TEAM_LABEL[team]
    emoji = "🅰️" if team is TeamId.A else "🅱️"
    flavor = random.choice(
        [
            "монетка крутится в петле времени…",
            "тахион сорвался с лезвия…",
            "стрелки часов закрутились…",
            "вектор времени дрогнул…",
        ]
    )
    await message.answer(
        f"🪙 <i>{flavor}</i>\n\n"
        f"Первый ход — <b>{emoji} {label}</b>.",
        parse_mode="HTML",
    )
