"""Pick a random arena background image for a match.

The visual arena is purely cosmetic — gameplay zones (acceleration, slowdown,
normal, inversion, chaos) are rolled independently each round per the lore.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

from ..config import ARENAS_DIR


_ARENA_NAMES: dict[str, str] = {
    "01_toyland": "Тойленд",
    "02_obsidian": "Обсидиан",
    "03_desert": "Пустыня",
    "04_overgrown": "Заросший храм",
}


_SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def list_arena_files() -> list[Path]:
    if not ARENAS_DIR.exists():
        return []
    return sorted(
        p
        for p in ARENAS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTS
    )


def pick_random_arena(rng: random.Random | None = None) -> Optional[Path]:
    rng = rng or random
    files = list_arena_files()
    if not files:
        return None
    return rng.choice(files)


def arena_display_name(arena_path: str | Path) -> str:
    stem = Path(arena_path).stem
    return _ARENA_NAMES.get(stem, stem.replace("_", " ").title())
