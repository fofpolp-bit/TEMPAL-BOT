"""Four player abilities from the Темпал lore.

Per lore: «Не всегда вашему персонажу может выпасть способность — всё решает
рандом.» So some players may get no ability at all (handled in rolls.py).

There are no rarity tiers — all four abilities are equal in pool.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TargetType(str, Enum):
    NONE = "none"  # affects caster
    ALLY = "ally"
    ENEMY = "enemy"
    ANY_PLAYER = "any"


@dataclass(frozen=True)
class Ability:
    id: str
    name: str
    emoji: str
    target: TargetType
    description: str
    flavor: str  # in-world text shown when the ability fires successfully


ABILITIES: list[Ability] = [
    Ability(
        id="acceleration",
        name="Ускорение",
        emoji="⚡",
        target=TargetType.ALLY,
        description=(
            "Увеличивает скорость союзника в этом раунде. "
            "Сила зависит от твоего ролла: частичный успех → +1, полный → +4."
        ),
        flavor="секунды сгущаются вокруг союзника и пропускают его вперёд",
    ),
    Ability(
        id="slowdown",
        name="Замедление",
        emoji="🐌",
        target=TargetType.ENEMY,
        description=(
            "Снижает скорость действий противника в этом раунде. "
            "Сила зависит от твоего ролла: частичный → −1, полный → −4."
        ),
        flavor="время вокруг цели густеет, как смола",
    ),
    Ability(
        id="freeze",
        name="Заморозка",
        emoji="❄️",
        target=TargetType.ENEMY,
        description=(
            "Останавливает время для противника. Частичный успех — снимает с "
            "цели все бонусы в этом раунде. Полный — цель пропускает следующий "
            "раунд целиком."
        ),
        flavor="время вокруг противника застывает кристаллом",
    ),
    Ability(
        id="inversion",
        name="Инверсия",
        emoji="🔁",
        target=TargetType.ANY_PLAYER,
        description=(
            "Заставляет цель «двигаться назад во времени»: её ролл этого "
            "раунда инвертируется (21 − ролл). Частичный успех — только число; "
            "полный — переворачиваются и бонусы (плюсы становятся минусами)."
        ),
        flavor="секунды цели идут вспять",
    ),
]


ABILITY_INDEX: dict[str, Ability] = {a.id: a for a in ABILITIES}


def get_ability(ability_id: str) -> Ability:
    return ABILITY_INDEX[ability_id]
