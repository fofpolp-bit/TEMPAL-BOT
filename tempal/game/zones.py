"""Arena zones — temporal effects that modify how the round plays out."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ZoneId(str, Enum):
    ACCELERATION = "acceleration"
    SLOWDOWN = "slowdown"
    NORMAL = "normal"
    INVERSION = "inversion"
    CHAOS = "chaos"


@dataclass(frozen=True)
class Zone:
    id: ZoneId
    name: str
    emoji: str
    description: str
    roll_bonus: int = 0  # baseline d20 modifier applied to every action
    inverts_rolls: bool = False  # if True, high roll = bad and vice versa


ZONES: dict[ZoneId, Zone] = {
    ZoneId.ACCELERATION: Zone(
        id=ZoneId.ACCELERATION,
        name="Ускорение времени",
        emoji="⏩",
        description="Время идёт в два раза быстрее. Все действия получают +1 к роллу.",
        roll_bonus=1,
    ),
    ZoneId.SLOWDOWN: Zone(
        id=ZoneId.SLOWDOWN,
        name="Замедление времени",
        emoji="⏪",
        description="Время идёт вдвое медленнее. Все действия получают −1 к роллу.",
        roll_bonus=-1,
    ),
    ZoneId.NORMAL: Zone(
        id=ZoneId.NORMAL,
        name="Нормальное время",
        emoji="⏱️",
        description="Никаких временных аномалий. Чисто, спокойно… подозрительно.",
        roll_bonus=0,
    ),
    ZoneId.INVERSION: Zone(
        id=ZoneId.INVERSION,
        name="Инверсия времени",
        emoji="🔄",
        description="Время идёт назад. Роллы инвертируются: 20→1, 1→20.",
        inverts_rolls=True,
    ),
    ZoneId.CHAOS: Zone(
        id=ZoneId.CHAOS,
        name="Хаотичное время",
        emoji="🌪️",
        description="Хаос. Эффект зоны меняется каждые 1–5 минут реального времени.",
    ),
}


def get_zone(zone_id: ZoneId) -> Zone:
    return ZONES[zone_id]


def all_zones() -> list[Zone]:
    return list(ZONES.values())


def non_chaos_zones() -> list[Zone]:
    return [z for z in ZONES.values() if z.id is not ZoneId.CHAOS]
