"""Admin commands available only to the lobby owner / group owner."""

from __future__ import annotations

import logging
import time

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

from ..game.models import ActionKind, GameStatus, PendingAction
from ..services.chronicle import build_match_chronicle
from .common import BotContext, fmt_player_name, get_context, is_group_owner_or_admin
from .keyboards import end_game_keyboard

logger = logging.getLogger(__name__)
router = Router(name="admin")


@router.message(Command("pause"))
async def cmd_pause(message: Message, bot: Bot, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    if not game or game.status is not GameStatus.PLAYING:
        await message.answer("Нечего ставить на паузу.")
        return
    allowed = (
        message.from_user.id == game.owner_id
        or await is_group_owner_or_admin(bot, message.chat.id, message.from_user.id)
    )
    if not allowed:
        await message.answer("Пауза доступна только владельцу группы или создателю лобби.")
        return
    game.status = GameStatus.PAUSED
    ctx.store.put(game)
    await message.answer("⏸ Матч поставлен на паузу. /resume чтобы продолжить.")


@router.message(Command("resume"))
async def cmd_resume(message: Message, bot: Bot, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    if not game or game.status is not GameStatus.PAUSED:
        await message.answer("Нечего возобновлять.")
        return
    allowed = (
        message.from_user.id == game.owner_id
        or await is_group_owner_or_admin(bot, message.chat.id, message.from_user.id)
    )
    if not allowed:
        await message.answer("Возобновить может только владелец группы или создатель лобби.")
        return
    game.status = GameStatus.PLAYING
    ctx.store.put(game)
    await message.answer("▶️ Матч возобновлён.")


@router.message(Command("skip"))
async def cmd_skip(message: Message, bot: Bot, **kwargs) -> None:
    """Force-reannounce the current round.

    Use this if the bot gets stuck mid-animation or skips the round header
    after a previous flood-control hiccup. Owner / group admin only.
    """
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    if not game or game.status is not GameStatus.PLAYING:
        await message.answer("Сейчас нет активного раунда — пропускать нечего.")
        return
    allowed = (
        message.from_user.id == game.owner_id
        or await is_group_owner_or_admin(bot, message.chat.id, message.from_user.id)
    )
    if not allowed:
        await message.answer(
            "Команду /skip может вызвать только владелец группы или создатель лобби."
        )
        return
    # Wipe any half-collected actions/notes for the current round and
    # re-issue the round header + action prompts. The actual game logic
    # is unchanged — only the round bootstrap is replayed.
    game.pending_actions = {}
    game.round_rp_notes = {}
    ctx.store.put(game)
    from .gameplay import start_round, _STATUS_MSG_IDS

    _STATUS_MSG_IDS.pop(game.chat_id, None)
    await message.answer(
        f"⏭ Перезапускаю раунд <b>{game.current_round}</b>…",
        parse_mode="HTML",
    )
    await start_round(bot, ctx, game)


@router.message(Command("endgame"))
async def cmd_endgame(message: Message, bot: Bot, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    if not game:
        await message.answer("Здесь нет активного матча.")
        return
    allowed = (
        message.from_user.id == game.owner_id
        or await is_group_owner_or_admin(bot, message.chat.id, message.from_user.id)
    )
    if not allowed:
        await message.answer("Только владелец группы или создатель лобби может завершить матч.")
        return
    game.status = GameStatus.FINISHED
    game.finished_at = time.time()
    from ..game.storage import record_profiles_for_match

    record_profiles_for_match(ctx.profiles, game)
    ctx.store.put(game)
    # Persist a per-chat snapshot so /history and the post-match buttons
    # keep working even after the next /newgame replaces this game in
    # the main store.
    ctx.last_match.put(game)
    await message.answer(
        build_match_chronicle(game),
        parse_mode="HTML",
        reply_markup=end_game_keyboard(),
    )


@router.message(Command("forcepass"))
async def cmd_forcepass(message: Message, bot: Bot, **kwargs) -> None:
    """Owner / group admin replies to a player's message with /forcepass
    to force-pass that player in the current round (when they're AFK
    and the round can't resolve)."""
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    if not game or game.status is not GameStatus.PLAYING:
        await message.answer("/forcepass работает только во время активного раунда.")
        return
    if not (message.reply_to_message and message.reply_to_message.from_user):
        await message.answer(
            "Ответь /forcepass на сообщение игрока, которому надо засчитать пас."
        )
        return
    allowed = (
        message.from_user.id == game.owner_id
        or await is_group_owner_or_admin(bot, message.chat.id, message.from_user.id)
    )
    if not allowed:
        await message.answer(
            "Только владелец группы или создатель лобби может форсить пас."
        )
        return
    target = message.reply_to_message.from_user
    player = game.players.get(target.id)
    if not player or not player.is_eligible():
        await message.answer("Этот игрок не в матче.")
        return
    if player.frozen_rounds_left > 0:
        await message.answer(
            f"❄️ {fmt_player_name(player.name)} и так пропускает раунд."
        )
        return

    # Force-fill both the action slot and the RP-text slot so the
    # readiness check passes and the round can resolve.
    game.pending_actions[player.user_id] = PendingAction(
        user_id=player.user_id,
        kind=ActionKind.PASS,
        note="(вынужденный пас)",
    )
    game.round_rp_notes.setdefault(player.user_id, "(пропустил раунд)")
    ctx.store.put(game)

    await message.answer(
        f"⏸ {fmt_player_name(player.name)} пасует этот раунд (по решению ведущего)."
    )

    # Try to nudge the round forward right away.
    from .gameplay import _maybe_resolve, _post_readiness_status

    try:
        await _post_readiness_status(bot, game)
    except Exception:
        logger.exception("forcepass: readiness status update failed")
    try:
        await _maybe_resolve(bot, ctx, game)
    except Exception:
        logger.exception("forcepass: maybe_resolve failed")
