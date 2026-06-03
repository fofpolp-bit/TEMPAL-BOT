"""The actual match flow: rounds, action collection, resolution, scoring."""

from __future__ import annotations

import asyncio
import html
import logging
import random
import re
import time
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ..game.abilities import TargetType
from ..game.models import (
    ActionKind,
    Game,
    GameStatus,
    PendingAction,
    Player,
    TEAM_LABEL,
    TeamId,
)
from ..game.resolver import RoundResolution, resolve_round
from ..game.rolls import random_sphere_position
from ..game.zones import ZoneId, all_zones
from ..services.arena_map import render_with_legend
from ..services.chaos import maybe_update, roll_new_effect
from ..services.dice import animate_roll
from ..services.chronicle import build_match_chronicle
from .common import BotContext, fmt_player_name, format_round_header, format_scoreboard, get_context
from .keyboards import action_keyboard, end_game_keyboard, target_keyboard

logger = logging.getLogger(__name__)
router = Router(name="gameplay")


# Map for in-flight per-player action drafts: (chat_id, user_id) -> draft.
# Lives only in memory — they're transient (one round).
_DRAFTS: dict[tuple[int, int], "ActionDraft"] = {}

# In-memory tracker for the chat's "readiness" status message so we can edit
# it in place instead of spamming the chat. chat_id -> message_id.
_STATUS_MSG_IDS: dict[int, int] = {}

# Hashtag the bot looks for in chat messages to recognise an RP description.
_RP_HASHTAG_RE = re.compile(r"#(темпал|tempal)\b", re.IGNORECASE)


class ActionDraft:
    """Builds up a PendingAction as the player picks options in inline keyboards."""

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        self.kind: Optional[ActionKind] = None
        self.target_user_id: Optional[int] = None

    def to_pending(self) -> PendingAction:
        assert self.kind is not None
        return PendingAction(
            user_id=self.user_id,
            kind=self.kind,
            target_user_id=self.target_user_id,
        )


# ── round bootstrap ──────────────────────────────────────────────────


async def start_round(bot: Bot, ctx: BotContext, game: Game) -> None:
    """Set up the current round: pick zone, place sphere, post arena message."""
    # clear last round's RP notes
    game.round_rp_notes = {}

    # rotate zone — 30% chance to keep current, otherwise pick a new random zone
    if random.random() > 0.3:
        new_zone = random.choice(all_zones())
        game.current_zone_id = new_zone.id

    if game.current_zone_id is ZoneId.CHAOS:
        if game.chaos is None or time.time() >= game.chaos.next_change_at:
            game.chaos = roll_new_effect(ctx.settings)
    else:
        game.chaos = None

    game.sphere_position = random_sphere_position()
    game.pending_actions = {}
    ctx.store.put(game)

    # Announce round
    text = format_round_header(game)
    if game.chaos:
        text += (
            f"\n\n{game.chaos.effect_emoji} <b>Хаос текущей минуты:</b> "
            f"<i>{game.chaos.effect_label}</i>"
        )
    text += (
        "\n\n✍️ <b>Опишите ваш ход в чате с тегом</b> <code>#темпал</code>\n"
        "Например: <i>«Скольжу по льду к сфере, пытаюсь обогнать Боба #темпал»</i>\n"
        "Без тега бот не поймёт, что это твоё RP-описание.\n\n"
        "Потом тапни кнопку действия в личке (или /act здесь).\n"
        "⏳ <b>Время не ограничено</b> — раунд пойдёт дальше, когда все напишут и нажмут кнопки."
    )
    msg = await bot.send_message(game.chat_id, text, parse_mode="HTML")
    game.round_message_id = msg.message_id
    ctx.store.put(game)

    # offer keyboards to each eligible player via DM (fall back to group)
    for player in game.eligible_players():
        await _send_action_prompt(bot, game, player)

    # reset status message so a fresh one is posted for this round
    _STATUS_MSG_IDS.pop(game.chat_id, None)
    await _post_readiness_status(bot, game)


async def _send_action_prompt(bot: Bot, game: Game, player: Player) -> None:
    if player.frozen_rounds_left > 0:
        try:
            await bot.send_message(
                player.user_id,
                f"❄️ Ты заморожен(а) — пропускаешь раунд {game.current_round}.",
            )
        except Exception:
            pass
        return
    try:
        await bot.send_message(
            player.user_id,
            f"⚔️ Раунд {game.current_round}. Выбирай действие:",
            reply_markup=action_keyboard(player),
        )
    except Exception:
        # user hasn't started the bot in DM
        pass


# ── /act ─────────────────────────────────────────────────────────────


@router.message(Command("act"))
async def cmd_act(message: Message, bot: Bot, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    target_game: Optional[Game]
    if game and game.status is GameStatus.PLAYING:
        target_game = game
    else:
        target_game = _find_game_for_user(ctx, message.from_user.id)
    if not target_game or target_game.status is not GameStatus.PLAYING:
        await message.answer("Сейчас нет активного раунда для тебя.")
        return
    player = target_game.players.get(message.from_user.id)
    if not player or not player.is_eligible():
        await message.answer("Ты не участник матча.")
        return
    if player.frozen_rounds_left > 0:
        await message.answer("❄️ Ты заморожен(а) — этот раунд пропускаешь.")
        return
    await message.answer(
        f"Раунд {target_game.current_round}. Выбирай действие:",
        reply_markup=action_keyboard(player),
    )


# ── action callbacks ────────────────────────────────────────────────


@router.callback_query(F.data == "act:move")
async def cb_act_move(call: CallbackQuery, bot: Bot, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = _find_game_for_user(ctx, call.from_user.id)
    if not _ensure_can_act(call, game):
        return
    draft = _draft(call.from_user.id)
    draft.kind = ActionKind.MOVE_TO_SPHERE
    draft.target_user_id = None
    _submit_draft(ctx, game, draft)
    await call.message.edit_text(
        "🏃 Действие зафиксировано: бросок к сфере.\nЖду остальных игроков…"
    )
    await call.answer()
    await _prompt_text_if_missing(bot, game, call.from_user.id)
    await _post_readiness_status(bot, game)
    await _maybe_resolve(bot, ctx, game)


@router.callback_query(F.data == "act:pass")
async def cb_act_pass(call: CallbackQuery, bot: Bot, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = _find_game_for_user(ctx, call.from_user.id)
    if not _ensure_can_act(call, game):
        return
    draft = _draft(call.from_user.id)
    draft.kind = ActionKind.PASS
    draft.target_user_id = None
    _submit_draft(ctx, game, draft)
    await call.message.edit_text("⏸ Действие зафиксировано: пас.")
    await call.answer()
    await _prompt_text_if_missing(bot, game, call.from_user.id)
    await _post_readiness_status(bot, game)
    await _maybe_resolve(bot, ctx, game)


@router.callback_query(F.data == "act:attack")
async def cb_act_attack(call: CallbackQuery, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = _find_game_for_user(ctx, call.from_user.id)
    if not _ensure_can_act(call, game):
        return
    actor = game.players[call.from_user.id]
    draft = _draft(call.from_user.id)
    draft.kind = ActionKind.ATTACK
    kb = target_keyboard(
        game, actor, want_enemy=True, want_ally=False, kind=ActionKind.ATTACK
    )
    await call.message.edit_text("⚔️ Кого атакуем?", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data == "act:ability")
async def cb_act_ability(call: CallbackQuery, bot: Bot, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = _find_game_for_user(ctx, call.from_user.id)
    if not _ensure_can_act(call, game):
        return
    actor = game.players[call.from_user.id]
    ability = actor.ability
    if not ability:
        await call.answer("У тебя нет способности.", show_alert=True)
        return
    draft = _draft(call.from_user.id)
    draft.kind = ActionKind.USE_ABILITY
    if ability.target is TargetType.NONE:
        draft.target_user_id = None
        _submit_draft(ctx, game, draft)
        await call.message.edit_text(
            f"{ability.emoji} Зафиксировано: <b>{ability.name}</b>.",
            parse_mode="HTML",
        )
        await call.answer()
        await _prompt_text_if_missing(bot, game, call.from_user.id)
        await _post_readiness_status(bot, game)
        await _maybe_resolve(bot, ctx, game)
        return
    want_enemy = ability.target in (TargetType.ENEMY, TargetType.ANY_PLAYER)
    want_ally = ability.target in (TargetType.ALLY, TargetType.ANY_PLAYER)
    kb = target_keyboard(
        game, actor, want_enemy=want_enemy, want_ally=want_ally, kind=ActionKind.USE_ABILITY
    )
    await call.message.edit_text(
        f"{ability.emoji} На кого применить «{ability.name}»?", reply_markup=kb
    )
    await call.answer()


@router.callback_query(F.data == "act:back")
async def cb_act_back(call: CallbackQuery, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = _find_game_for_user(ctx, call.from_user.id)
    if not game or game.status is not GameStatus.PLAYING:
        await call.answer()
        return
    actor = game.players.get(call.from_user.id)
    if not actor:
        await call.answer()
        return
    await call.message.edit_text(
        "Выбирай действие:", reply_markup=action_keyboard(actor)
    )
    await call.answer()


@router.callback_query(F.data.startswith("target:"))
async def cb_pick_target(call: CallbackQuery, bot: Bot, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = _find_game_for_user(ctx, call.from_user.id)
    if not _ensure_can_act(call, game):
        return
    parts = call.data.split(":")
    if len(parts) != 3:
        await call.answer()
        return
    kind = ActionKind(parts[1])
    target_id = int(parts[2])
    draft = _draft(call.from_user.id)
    draft.kind = kind
    draft.target_user_id = target_id
    _submit_draft(ctx, game, draft)
    target_name = game.players[target_id].name if target_id in game.players else "цель"
    if kind is ActionKind.ATTACK:
        await call.message.edit_text(f"⚔️ Зафиксировано: атака на {target_name}.")
    else:
        ability = game.players[call.from_user.id].ability
        emoji = ability.emoji if ability else ""
        name = ability.name if ability else "способность"
        await call.message.edit_text(
            f"{emoji} Зафиксировано: <b>{name}</b> на {fmt_player_name(target_name)}.",
            parse_mode="HTML",
        )
    await call.answer()
    await _prompt_text_if_missing(bot, game, call.from_user.id)
    await _post_readiness_status(bot, game)
    await _maybe_resolve(bot, ctx, game)


# ── helpers ──────────────────────────────────────────────────────────


def _draft(user_id: int) -> ActionDraft:
    key = (user_id,)
    draft = _DRAFTS.get((0, user_id))  # use 0 as placeholder chat id
    if draft is None:
        draft = ActionDraft(user_id)
        _DRAFTS[(0, user_id)] = draft
    return draft


def _submit_draft(ctx: BotContext, game: Game, draft: ActionDraft) -> None:
    game.pending_actions[draft.user_id] = draft.to_pending()
    ctx.store.put(game)
    _DRAFTS.pop((0, draft.user_id), None)


def _find_game_for_user(ctx: BotContext, user_id: int) -> Optional[Game]:
    for g in ctx.store.all_games():
        if g.status is GameStatus.PLAYING and user_id in g.players:
            return g
    return None


def _ensure_can_act(call: CallbackQuery, game: Optional[Game]) -> bool:
    if not game or game.status is not GameStatus.PLAYING:
        asyncio.create_task(call.answer("Сейчас нет активного раунда.", show_alert=True))
        return False
    player = game.players.get(call.from_user.id)
    if not player or not player.is_eligible():
        asyncio.create_task(call.answer("Ты не участник матча.", show_alert=True))
        return False
    if player.frozen_rounds_left > 0:
        asyncio.create_task(call.answer("❄️ Ты заморожен(а)", show_alert=True))
        return False
    return True


def _has_text(game: Game, user_id: int) -> bool:
    return user_id in game.round_rp_notes


def _has_action(game: Game, user_id: int) -> bool:
    return user_id in game.pending_actions


def _player_ready(game: Game, player: Player) -> bool:
    if player.frozen_rounds_left > 0:
        return True
    return _has_action(game, player.user_id) and _has_text(game, player.user_id)


async def _prompt_text_if_missing(bot: Bot, game: Game, user_id: int) -> None:
    """DM the player asking for the #темпал text if they've clicked a
    button but haven't written their RP description yet."""
    if _has_text(game, user_id):
        return
    try:
        await bot.send_message(
            user_id,
            "✍️ Действие принято. Теперь опиши свой ход в чате группы с тегом "
            "<b>#темпал</b> — без этого бот не засчитает твоё RP-описание.",
            parse_mode="HTML",
        )
    except Exception:
        # fall back to group chat — ping by name
        player = game.players.get(user_id)
        if player is None:
            return
        try:
            await bot.send_message(
                game.chat_id,
                f"✍️ {fmt_player_name(player.name)}, опиши свой ход с тегом "
                "<b>#темпал</b>.",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("failed to nudge player for RP text")


def _readiness_summary(game: Game) -> tuple[int, int, list[str], list[str]]:
    eligible = list(game.eligible_players())
    ready, waiting = [], []
    for p in eligible:
        if p.frozen_rounds_left > 0:
            continue  # frozen players don't participate
        if _player_ready(game, p):
            ready.append(p.name)
        else:
            missing = []
            if not _has_action(game, p.user_id):
                missing.append("кнопка")
            if not _has_text(game, p.user_id):
                missing.append("#темпал")
            waiting.append(f"{p.name} ({', '.join(missing)})")
    return len(ready), len(ready) + len(waiting), ready, waiting


async def _post_readiness_status(bot: Bot, game: Game) -> None:
    """Post or edit a single status message showing who has submitted."""
    if game.status is not GameStatus.PLAYING:
        return
    ready_n, total, ready, waiting = _readiness_summary(game)
    if total == 0:
        return
    lines = [f"⏳ <b>Готовы {ready_n}/{total}</b>"]
    if ready:
        lines.append("✅ " + ", ".join(_escape(n) for n in ready))
    if waiting:
        lines.append("… ждём: " + ", ".join(_escape(n) for n in waiting))
    text = "\n".join(lines)
    existing = _STATUS_MSG_IDS.get(game.chat_id)
    try:
        if existing:
            await bot.edit_message_text(
                text,
                chat_id=game.chat_id,
                message_id=existing,
                parse_mode="HTML",
            )
        else:
            msg = await bot.send_message(game.chat_id, text, parse_mode="HTML")
            _STATUS_MSG_IDS[game.chat_id] = msg.message_id
    except Exception:
        # message couldn't be edited (maybe deleted) — try a fresh one
        try:
            msg = await bot.send_message(game.chat_id, text, parse_mode="HTML")
            _STATUS_MSG_IDS[game.chat_id] = msg.message_id
        except Exception:
            logger.exception("failed to post readiness status")


async def _maybe_resolve(bot: Bot, ctx: BotContext, game: Game) -> None:
    eligible = list(game.eligible_players())
    if all(_player_ready(game, p) for p in eligible):
        # clear the status message — round is resolving now
        _STATUS_MSG_IDS.pop(game.chat_id, None)
        await _force_resolve(bot, ctx, game)


async def _force_resolve(bot: Bot, ctx: BotContext, game: Game) -> None:
    if game.status is not GameStatus.PLAYING:
        return

    # Attach captured RP notes onto each pending action so they're persisted
    # with the round outcome.
    for user_id, action in game.pending_actions.items():
        if action.note is None and user_id in game.round_rp_notes:
            action.note = game.round_rp_notes[user_id]

    resolution = resolve_round(game)

    # Cinematic rolls — but throttled to avoid Telegram flood control.
    # We pace ourselves at ~1.2s per player, well under Telegram's per-chat rate.
    summary_lines: list[str] = []
    for report in resolution.reports:
        rp_note = _rp_note_for(report, game)
        if report.roll is not None:
            title = f"{report.player_name} — {_action_title(report)}"
            prefix = ""
            if rp_note:
                prefix = f"<i>«{_escape(rp_note)}»</i>"
            try:
                await animate_roll(
                    bot,
                    game.chat_id,
                    title=title,
                    result=report.roll,
                    frame_delay=0.7,
                    text_prefix=prefix,
                )
            except Exception:
                logger.exception("animate_roll failed for %s", report.player_name)
                # send a static fallback instead so the round still narrates
                try:
                    body = prefix + ("\n" if prefix else "")
                    body += f"<b>{_escape(title)}</b>\n🎲 <b>{report.roll.final}</b> — {report.success_summary}"
                    await bot.send_message(game.chat_id, body, parse_mode="HTML")
                except Exception:
                    logger.exception("fallback send_message failed")
            await asyncio.sleep(1.2)
        elif rp_note:
            try:
                await bot.send_message(
                    game.chat_id,
                    f"<i>«{_escape(rp_note)}»</i>\n{report.success_summary}",
                    parse_mode="HTML",
                )
                await asyncio.sleep(0.6)
            except Exception:
                logger.exception("rp-note pass send failed")
        summary_lines.append(report.success_summary)

    sb = format_scoreboard(game)
    round_points = ""
    if resolution.points_a or resolution.points_b:
        round_points = (
            f"\n\n💠 Очки за раунд: 🅰️ +{resolution.points_a} · 🅱️ +{resolution.points_b}"
        )
    body = "\n".join(summary_lines)

    await bot.send_message(
        game.chat_id,
        f"📜 <b>Итоги раунда {game.current_round}</b>\n\n{body}{round_points}\n\n{sb}",
        parse_mode="HTML",
    )

    # New achievements
    for user_id, ach_id in resolution.new_achievements:
        from ..game.achievements import get_achievement

        ach = get_achievement(ach_id)
        p = game.players.get(user_id)
        if not p:
            continue
        await bot.send_message(
            game.chat_id,
            f"🏆 <b>{fmt_player_name(p.name)}</b> получает достижение "
            f"{ach.emoji} <b>{ach.name}</b> — <i>{ach.description}</i>",
            parse_mode="HTML",
        )

    if resolution.winner is not None:
        game.status = GameStatus.FINISHED
        game.winner = resolution.winner
        game.finished_at = time.time()
        from ..game.storage import record_profiles_for_match

        record_profiles_for_match(ctx.profiles, game)
        ctx.store.put(game)
        # Per-chat snapshot of the last finished match — survives /newgame.
        ctx.last_match.put(game)
        await bot.send_message(
            game.chat_id,
            build_match_chronicle(game),
            parse_mode="HTML",
            reply_markup=end_game_keyboard(),
        )
        return

    game.current_round += 1
    ctx.store.put(game)
    await asyncio.sleep(1.5)
    await start_round(bot, ctx, game)


def _action_title(report) -> str:
    if report.action.kind is ActionKind.MOVE_TO_SPHERE:
        return "бросок к сфере"
    if report.action.kind is ActionKind.ATTACK:
        return f"атака на {report.target_name or 'противника'}"
    if report.action.kind is ActionKind.USE_ABILITY and report.ability is not None:
        target = f" на {report.target_name}" if report.target_name else ""
        return f"{report.ability.name}{target}"
    return "действие"


def _rp_note_for(report, game: Game) -> Optional[str]:
    note = report.action.note
    if note:
        return note
    return game.round_rp_notes.get(report.player_id)


def _escape(text: str) -> str:
    return html.escape(text)


# ── chat capture: free-text RP descriptions ──────────────────────────


@router.message(
    F.chat.type.in_({"group", "supergroup"}),
    F.text,
    ~F.text.startswith("/"),
)
async def capture_rp_text(message: Message, bot: Bot, **kwargs) -> None:
    """Capture RP descriptions tagged with #темпал from the group chat.

    Only messages containing the hashtag #темпал (or #tempal) are saved as
    a player's RP narration for the current round. The hashtag is stripped
    from the stored text so the quote reads cleanly.
    """
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    if not game or game.status is not GameStatus.PLAYING:
        return
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return
    player = game.players.get(user_id)
    if not player or not player.is_eligible() or player.frozen_rounds_left > 0:
        return
    raw = (message.text or "").strip()
    if not _RP_HASHTAG_RE.search(raw):
        return  # no tag — just chatter, ignore
    cleaned = _RP_HASHTAG_RE.sub("", raw).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) < 2:
        # message was basically just the tag — keep something readable
        cleaned = raw
    if len(cleaned) > 400:
        cleaned = cleaned[:400].rstrip() + "…"
    game.round_rp_notes[user_id] = cleaned
    ctx.store.put(game)
    # update status & maybe resolve now that this player might be ready
    await _post_readiness_status(bot, game)
    await _maybe_resolve(bot, ctx, game)
    ctx.store.put(game)
