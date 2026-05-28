"""Dice rolling and random ability assignment.

Per the Темпал lore, a d20 has three outcome tiers:
    1–5   неудача
    6–15  частичный успех
    16–20 полный успех

No critical fail / critical success — these are pure tiers.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum


class RollOutcome(str, Enum):
    FAIL = "fail"
    PARTIAL = "partial"
    SUCCESS = "success"


OUTCOME_LABEL: dict[RollOutcome, str] = {
    RollOutcome.FAIL: "❌ Неудача",
    RollOutcome.PARTIAL: "🟡 Частичный успех",
    RollOutcome.SUCCESS: "✅ Полный успех",
}


# Probability that any given player gets NO ability at all when distributing
# abilities at match start. Lore: «Не всегда вашему персонажу может выпасть
# способность — всё решает рандом.»
NO_ABILITY_CHANCE = 0.3


@dataclass
class RollResult:
    raw: int  # natural 1-20 BEFORE inversions / modifiers
    modifiers: list[tuple[str, int]]  # (label, value)
    final: int  # final tier-determining number
    outcome: RollOutcome

    @property
    def total_modifier(self) -> int:
        return sum(v for _, v in self.modifiers)


def classify(final: int) -> RollOutcome:
    """Classify by the final number, per lore tiers."""
    if final <= 5:
        return RollOutcome.FAIL
    if final <= 15:
        return RollOutcome.PARTIAL
    return RollOutcome.SUCCESS


def roll_d20(
    modifiers: list[tuple[str, int]] | None = None,
    *,
    invert: bool = False,
    rng: random.Random | None = None,
) -> RollResult:
    """Roll a d20 with named modifiers.

    ``invert``: if True, the natural roll is mirrored (21 − r) — used for the
    Inversion zone or the Inversion ability.
    """
    rng = rng or random
    natural = rng.randint(1, 20)
    if invert:
        natural = 21 - natural
    mods = list(modifiers or [])
    final = max(1, min(30, natural + sum(v for _, v in mods)))
    return RollResult(
        raw=natural,
        modifiers=mods,
        final=final,
        outcome=classify(final),
    )


def assign_random_abilities(
    player_ids: list[int],
    *,
    rng: random.Random | None = None,
) -> dict[int, str | None]:
    """Distribute abilities. Each player has a chance of getting nothing.

    Returns a mapping user_id → ability_id (or None for «no ability»).
    """
    from .abilities import ABILITIES

    rng = rng or random
    assignments: dict[int, str | None] = {}
    for pid in player_ids:
        if rng.random() < NO_ABILITY_CHANCE:
            assignments[pid] = None
        else:
            assignments[pid] = rng.choice(ABILITIES).id
    return assignments


def random_sphere_position(rng: random.Random | None = None) -> tuple[int, int]:
    """Roll a 5×5 grid cell for where the sphere appears (lore: roll 5 for x/y)."""
    rng = rng or random
    return (rng.randint(0, 4), rng.randint(0, 4))
