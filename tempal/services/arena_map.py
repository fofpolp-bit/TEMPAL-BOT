"""Emoji-based renderer for the arena map shown each round."""

from __future__ import annotations

from ..game.models import Game, TeamId
from ..game.zones import ZoneId

# 5x5 grid base
GRID_SIZE = 5

ZONE_TILE = {
    ZoneId.ACCELERATION: "🟦",
    ZoneId.SLOWDOWN: "🟪",
    ZoneId.NORMAL: "⬜",
    ZoneId.INVERSION: "🟫",
    ZoneId.CHAOS: "🟥",
}

SPHERE_EMOJI = "🔮"
TEAM_TILE = {TeamId.A: "🅰️", TeamId.B: "🅱️"}


def render(game: Game) -> str:
    tile = ZONE_TILE[game.current_zone_id]
    sx, sy = game.sphere_position
    rows: list[str] = []
    for y in range(GRID_SIZE):
        row = []
        for x in range(GRID_SIZE):
            if (x, y) == (sx, sy):
                row.append(SPHERE_EMOJI)
            else:
                row.append(tile)
        rows.append("".join(row))
    return "\n".join(rows)


def render_with_legend(game: Game) -> str:
    zone = game.zone
    body = render(game)
    legend = f"{zone.emoji} <b>Зона:</b> {zone.name}\n{zone.description}"
    return f"{legend}\n\n{body}"
