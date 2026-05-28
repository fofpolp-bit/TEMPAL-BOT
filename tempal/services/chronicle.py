"""Build the end-of-match chronicle text."""

from __future__ import annotations

import html
from typing import Iterable

from ..game.achievements import get_achievement
from ..game.models import Game, TEAM_LABEL, TeamId
from ..game.zones import get_zone


def build_match_chronicle(game: Game) -> str:
    a = game.teams[TeamId.A]
    b = game.teams[TeamId.B]
    winner_text = (
        f"🏆 Победитель — <b>{TEAM_LABEL[game.winner]}</b> с {game.teams[game.winner].score} очками!"
        if game.winner
        else "🤝 Матч завершён без явного победителя."
    )

    lines = [
        "📖 <b>Хроника матча Темпал</b>",
        f"{a.emoji} {a.name}: <b>{a.score}</b>   ·   {b.emoji} {b.name}: <b>{b.score}</b>",
        "",
        winner_text,
        "",
    ]

    # Memorable round highlights — pick the highest-swing rounds
    rounds = sorted(
        game.chronicle,
        key=lambda r: abs(r.points_a - r.points_b),
        reverse=True,
    )[:5]
    if rounds:
        lines.append("<b>Самые громкие раунды:</b>")
        for r in sorted(rounds, key=lambda x: x.number):
            zone = get_zone(r.zone_id)
            lines.append(
                f"Раунд {r.number} — {zone.emoji} {zone.name}. "
                f"Счёт после: {r.score_after[0]}:{r.score_after[1]}."
            )
        lines.append("")

    # Achievements summary
    ach_lines: list[str] = []
    for player in game.players.values():
        if not player.earned_achievements:
            continue
        items = ", ".join(
            f"{get_achievement(a).emoji} {get_achievement(a).name}"
            for a in player.earned_achievements
        )
        ach_lines.append(f"· <b>{html.escape(player.name)}</b>: {items}")
    if ach_lines:
        lines.append("🏅 <b>Достижения:</b>")
        lines.extend(ach_lines)

    return "\n".join(lines)


def build_round_log(reports: Iterable[str]) -> str:
    return "\n".join(reports)
