"""Chaotic zone — sub-effect that mutates every 1–5 minutes of real time."""

from __future__ import annotations

import random
import time

from ..config import Settings
from ..game.models import ChaosState

CHAOS_EFFECTS = [
    ("Ускорение", "⚡", "Время мчится — +2 к роллам."),
    ("Замедление", "🐌", "Всё замирает — −2 к роллам."),
    ("Инверсия", "🔁", "Роллы переворачиваются."),
    ("Спокойствие", "🌙", "Странная тишина — модификаторы не работают."),
    ("Резонанс", "🔔", "Криты считаются вдвойне."),
    ("Туман", "🌫️", "Цели для способностей выбираются случайно."),
]


def roll_new_effect(settings: Settings, rng: random.Random | None = None) -> ChaosState:
    rng = rng or random
    label, emoji, _desc = rng.choice(CHAOS_EFFECTS)
    seconds = rng.randint(settings.chaos_min_seconds, settings.chaos_max_seconds)
    return ChaosState(
        effect_label=label,
        effect_emoji=emoji,
        next_change_at=time.time() + seconds,
    )


def maybe_update(chaos: ChaosState, settings: Settings) -> tuple[ChaosState, bool]:
    """If the effect has expired, generate a new one. Returns (new_state, changed)."""
    if time.time() < chaos.next_change_at:
        return chaos, False
    return roll_new_effect(settings), True
