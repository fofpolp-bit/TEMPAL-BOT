"""Inline keyboards used across the bot."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..game.models import ActionKind, Game, Player, TeamId


# ── lobby ──────────────────────────────────────────────────────────────


TARGET_SCORE_OPTIONS: tuple[int, ...] = (15, 30, 50)


def lobby_keyboard(game: Game) -> InlineKeyboardMarkup:
    """Lobby controls: join, leave, change team, reserves, target score, start, cancel."""
    target_row = [
        InlineKeyboardButton(
            text=(f"✅ {score}" if game.target_score == score else f"🎯 {score}"),
            callback_data=f"lobby:target:{score}",
        )
        for score in TARGET_SCORE_OPTIONS
    ]
    rows = [
        [
            InlineKeyboardButton(text="✅ Войти", callback_data="lobby:join"),
            InlineKeyboardButton(text="🚪 Выйти", callback_data="lobby:leave"),
        ],
        [
            InlineKeyboardButton(text="🅰️ Команда А", callback_data="lobby:team:A"),
            InlineKeyboardButton(text="🅱️ Команда Б", callback_data="lobby:team:B"),
        ],
        [
            InlineKeyboardButton(text="🪑 Запас А", callback_data="lobby:spare:A"),
            InlineKeyboardButton(text="🪑 Запас Б", callback_data="lobby:spare:B"),
        ],
        [InlineKeyboardButton(text="🎲 Авто-распределение", callback_data="lobby:autoteams")],
        target_row,
        [InlineKeyboardButton(text="🚀 Начать игру", callback_data="lobby:start")],
        [InlineKeyboardButton(text="❌ Отменить лобби", callback_data="lobby:cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── round actions ──────────────────────────────────────────────────────


def action_keyboard(player: Player) -> InlineKeyboardMarkup:
    """Top-level action chooser shown to a single player in their private chat
    (or as inline reply in the group)."""
    buttons = [
        [InlineKeyboardButton(text="🏃 К сфере", callback_data="act:move")],
        [InlineKeyboardButton(text="⚔️ Атаковать", callback_data="act:attack")],
    ]
    if player.ability:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{player.ability.emoji} {player.ability.name}",
                    callback_data="act:ability",
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text="⏸ Пас", callback_data="act:pass")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def target_keyboard(
    game: Game, actor: Player, *, want_enemy: bool, want_ally: bool, kind: ActionKind
) -> InlineKeyboardMarkup:
    """Build a keyboard of valid targets for an action."""
    rows: list[list[InlineKeyboardButton]] = []
    for player in game.eligible_players():
        if player.user_id == actor.user_id:
            continue
        is_ally = player.team_id is actor.team_id
        if is_ally and not want_ally:
            continue
        if (not is_ally) and not want_enemy:
            continue
        emoji = "🅰️" if player.team_id is TeamId.A else "🅱️"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{emoji} {player.name}",
                    callback_data=f"target:{kind.value}:{player.user_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="act:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def end_game_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📖 Хроника матча", callback_data="end:chronicle")],
            [InlineKeyboardButton(text="🎴 Карточки", callback_data="end:cards")],
        ]
    )
