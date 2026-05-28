"""Shared helpers and dependencies for handlers."""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from typing import Optional

from aiogram import Bot

from ..config import Settings
from ..game.models import Game, TEAM_LABEL, TeamId
from ..game.storage import GameStore
from ..services.arena_map import render_with_legend

logger = logging.getLogger(__name__)


@dataclass
class BotContext:
    """Container injected into handlers via aiogram's data dict."""

    settings: Settings
    store: GameStore


def get_context(data: dict) -> BotContext:
    return data["ctx"]


def fmt_player_name(name: str) -> str:
    return html.escape(name)


def format_scoreboard(game: Game) -> str:
    a = game.teams[TeamId.A]
    b = game.teams[TeamId.B]
    return (
        f"{a.emoji} <b>{a.name}</b>: <b>{a.score}</b>   "
        f"·   {b.emoji} <b>{b.name}</b>: <b>{b.score}</b>"
    )


def format_round_header(game: Game) -> str:
    arena_block = render_with_legend(game)
    sb = format_scoreboard(game)
    return (
        f"⚔️ <b>Раунд {game.current_round}</b>\n"
        f"{sb}\n\n{arena_block}"
    )


def find_active_game_for_user(store: GameStore, user_id: int) -> Optional[Game]:
    for game in store.all_games():
        if user_id in game.players:
            return game
    return None


async def is_group_owner_or_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    return member.status in {"creator", "administrator"}
