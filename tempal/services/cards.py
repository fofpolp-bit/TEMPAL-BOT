"""Generate player cards — visual cards rendered with Pillow, plus a text fallback."""

from __future__ import annotations

import io
import logging
import textwrap
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from ..config import FONTS_DIR
from ..game.abilities import get_ability
from ..game.achievements import ACHIEVEMENTS, get_achievement
from ..game.models import LifetimeProfile, Player, TEAM_EMOJI, TEAM_LABEL, TeamId

logger = logging.getLogger(__name__)

CARD_WIDTH = 720
CARD_HEIGHT = 420

# Cyrillic-friendly fonts that are usually present on Debian/Ubuntu images.
FONT_CANDIDATES = [
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
    FONTS_DIR / "DejaVuSans-Bold.ttf",
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                continue
    return ImageFont.load_default()


TEAM_GRADIENTS = {
    TeamId.A: ((24, 90, 157), (10, 30, 70)),     # blue
    TeamId.B: ((157, 27, 60), (60, 10, 30)),     # crimson
    None: ((50, 50, 60), (15, 15, 25)),
}


def _vertical_gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, top)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        ratio = y / max(1, h - 1)
        r = int(top[0] + (bottom[0] - top[0]) * ratio)
        g = int(top[1] + (bottom[1] - top[1]) * ratio)
        b = int(top[2] + (bottom[2] - top[2]) * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    return img


def render_card(player: Player) -> bytes:
    """Render a player card to PNG bytes."""
    gradient = TEAM_GRADIENTS[player.team_id]
    img = _vertical_gradient((CARD_WIDTH, CARD_HEIGHT), *gradient)
    draw = ImageDraw.Draw(img)

    # decorative border
    draw.rectangle([(8, 8), (CARD_WIDTH - 9, CARD_HEIGHT - 9)], outline=(255, 255, 255), width=2)
    draw.rectangle([(14, 14), (CARD_WIDTH - 15, CARD_HEIGHT - 15)], outline=(255, 255, 255, 60), width=1)

    title_font = _font(40, bold=True)
    body_font = _font(26)
    small_font = _font(20)

    # Header
    draw.text((36, 32), "ТЕМПОРАЛЬНЫЙ ПАСПОРТ", font=title_font, fill="white")
    team_label = (
        TEAM_LABEL.get(player.team_id, "Запас") if player.team_id else "Без команды"
    )
    draw.text((36, 90), team_label, font=body_font, fill="white")

    # Name
    draw.text((36, 140), player.name, font=_font(34, bold=True), fill="white")

    # Ability
    if player.ability:
        ability = player.ability
        line = f"Способность: {ability.name}"
        draw.text((36, 200), line, font=body_font, fill="white")
        wrapped = textwrap.wrap(ability.description, width=58)
        for i, w in enumerate(wrapped[:3]):
            draw.text(
                (36, 234 + i * 28),
                w,
                font=small_font,
                fill=(220, 220, 230),
            )
    else:
        draw.text((36, 200), "Способность не назначена", font=body_font, fill="white")

    # Footer stats
    score_text = f"Личные очки: {player.personal_score}"
    stats_text = (
        f"Сфер взято: {player.sphere_captures} · "
        f"Нат. 20: {player.nat20s} · Нат. 1: {player.nat1s}"
    )
    draw.text((36, CARD_HEIGHT - 90), score_text, font=small_font, fill="white")
    draw.text((36, CARD_HEIGHT - 60), stats_text, font=small_font, fill="white")

    # Achievements row (text names only — emojis not in DejaVu font)
    if player.earned_achievements:
        names = ", ".join(
            get_achievement(a).name
            for a in player.earned_achievements
            if a in ACHIEVEMENTS
        )
        if names:
            wrapped = textwrap.wrap(f"Достижения: {names}", width=58)
            for i, w in enumerate(wrapped[:2]):
                draw.text(
                    (36, CARD_HEIGHT - 30 + i * 22),
                    w,
                    font=_font(16),
                    fill=(255, 230, 150),
                )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


PROFILE_GRADIENT = ((70, 50, 30), (25, 18, 10))  # warm bronze
ACCENT_BRONZE = (205, 130, 60)


def render_profile(profile: LifetimeProfile) -> bytes:
    """Render the out-of-game career card to PNG bytes."""
    img = _vertical_gradient((CARD_WIDTH, CARD_HEIGHT), *PROFILE_GRADIENT)
    draw = ImageDraw.Draw(img)

    draw.rectangle([(8, 8), (CARD_WIDTH - 9, CARD_HEIGHT - 9)], outline="white", width=2)
    draw.rectangle(
        [(14, 14), (CARD_WIDTH - 15, CARD_HEIGHT - 15)],
        outline=ACCENT_BRONZE,
        width=1,
    )

    title_font = _font(36, bold=True)
    body_font = _font(24)
    small_font = _font(20)
    micro_font = _font(16)

    draw.text((36, 28), "ТЕМПОРАЛЬНЫЙ ДОСЬЕ", font=title_font, fill="white")
    draw.text(
        (36, 78),
        profile.name or f"Игрок #{profile.user_id}",
        font=_font(32, bold=True),
        fill="white",
    )

    # Career line
    ratio = (
        f"{profile.matches_won}/{profile.matches_played}"
        if profile.matches_played
        else "ещё нет матчей"
    )
    draw.text(
        (36, 130),
        f"Матчей сыграно: {profile.matches_played}   ·   Побед: {ratio}",
        font=body_font,
        fill="white",
    )

    # Lifetime stats grid (two columns)
    stats_left = [
        ("Сфер взято", profile.total_sphere_captures),
        ("Натуральных 20", profile.total_nat20s),
        ("Натуральных 10", profile.total_nat10s),
        ("Натуральных 1", profile.total_nat1s),
    ]
    stats_right = [
        ("Заморозок", profile.total_successful_freezes),
        ("Способностей", profile.total_successful_ability_uses),
        ("Пасов", profile.total_passes),
        ("Раз заморожен", profile.total_times_fully_frozen),
    ]
    base_y = 175
    for i, (label, value) in enumerate(stats_left):
        draw.text((36, base_y + i * 28), f"{label}: {value}", font=small_font, fill="white")
    for i, (label, value) in enumerate(stats_right):
        draw.text(
            (CARD_WIDTH // 2, base_y + i * 28),
            f"{label}: {value}",
            font=small_font,
            fill="white",
        )

    # Bronzes
    if profile.achievement_counts:
        items = sorted(
            profile.achievement_counts.items(), key=lambda kv: -kv[1]
        )
        names = []
        for ach_id, count in items:
            if ach_id not in ACHIEVEMENTS:
                continue
            a = get_achievement(ach_id)
            names.append(f"{a.name} ×{count}" if count > 1 else a.name)
        if names:
            line = "🥉 Бронзы: " + ", ".join(names)
            wrapped = textwrap.wrap(line, width=64)
            for i, w in enumerate(wrapped[:3]):
                draw.text(
                    (36, CARD_HEIGHT - 80 + i * 22),
                    w,
                    font=micro_font,
                    fill=(255, 220, 160),
                )
    else:
        draw.text(
            (36, CARD_HEIGHT - 50),
            "🥉 Бронзы пока пустые — пора в матч.",
            font=small_font,
            fill=(220, 200, 160),
        )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_profile_text(profile: LifetimeProfile) -> str:
    """Fallback HTML text rendering of the lifetime profile."""
    lines = ["📜 <b>ТЕМПОРАЛЬНЫЙ ДОСЬЕ</b>"]
    lines.append(f"<b>{profile.name or f'Игрок #{profile.user_id}'}</b>")
    if profile.matches_played:
        lines.append(
            f"Матчей: <b>{profile.matches_played}</b> · "
            f"Побед: <b>{profile.matches_won}</b>"
        )
    else:
        lines.append("<i>Ещё ни одного завершённого матча.</i>")
    lines.append("")
    lines.append(
        f"Сфер взято: <b>{profile.total_sphere_captures}</b> · "
        f"Нат. 20: <b>{profile.total_nat20s}</b> · "
        f"Нат. 10: <b>{profile.total_nat10s}</b> · "
        f"Нат. 1: <b>{profile.total_nat1s}</b>"
    )
    lines.append(
        f"Заморозок: <b>{profile.total_successful_freezes}</b> · "
        f"Способностей: <b>{profile.total_successful_ability_uses}</b> · "
        f"Пасов: <b>{profile.total_passes}</b> · "
        f"Раз заморожен(а): <b>{profile.total_times_fully_frozen}</b>"
    )
    if profile.achievement_counts:
        ach_parts = []
        for ach_id, count in sorted(profile.achievement_counts.items(), key=lambda kv: -kv[1]):
            if ach_id not in ACHIEVEMENTS:
                continue
            a = get_achievement(ach_id)
            tag = f"{a.emoji} {a.name}"
            ach_parts.append(f"{tag}×{count}" if count > 1 else tag)
        if ach_parts:
            lines.append("")
            lines.append("🥉 <b>Бронзы:</b> " + ", ".join(ach_parts))
    return "\n".join(lines)


def render_card_text(player: Player) -> str:
    lines = ["🎴 <b>ТЕМПОРАЛЬНЫЙ ПАСПОРТ</b>"]
    team_label = TEAM_LABEL.get(player.team_id, "Запас") if player.team_id else "Без команды"
    team_emoji_text = TEAM_EMOJI.get(player.team_id, "·") if player.team_id else "·"
    lines.append(f"{team_emoji_text} {team_label}")
    lines.append(f"<b>{player.name}</b>")
    if player.ability:
        ab = player.ability
        lines.append(f"{ab.emoji} <b>{ab.name}</b>")
        lines.append(f"<i>{ab.description}</i>")
    else:
        lines.append("Способность не выпала — всё решает рандом.")
    lines.append("")
    lines.append(
        f"Очки: <b>{player.personal_score}</b> · Сфер: {player.sphere_captures} · "
        f"Нат. 20: {player.nat20s} · Нат. 1: {player.nat1s}"
    )
    if player.earned_achievements:
        ach_line = " ".join(
            f"{get_achievement(a).emoji} {get_achievement(a).name}"
            for a in player.earned_achievements
            if a
        )
        lines.append(f"🏆 {ach_line}")
    return "\n".join(lines)
