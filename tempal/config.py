"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
ARENAS_DIR = ROOT_DIR / "arenas"
FONTS_DIR = ROOT_DIR / "fonts"

DATA_DIR.mkdir(exist_ok=True)
ARENAS_DIR.mkdir(exist_ok=True)
FONTS_DIR.mkdir(exist_ok=True)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str = ""  # if set, used in place of JSON file
    # Chats where the bot is allowed to *operate* (run matches, post messages,
    # respond to commands in-chat). If empty, every chat is allowed.
    operate_chat_ids: frozenset[int] = field(default_factory=frozenset)
    # Chats whose membership grants DM access to the bot. If empty, every DM is
    # allowed. `operate_chat_ids` is implicitly included.
    allowed_chat_ids: frozenset[int] = field(default_factory=frozenset)
    state_file: Path = DATA_DIR / "games.json"
    arenas_dir: Path = ARENAS_DIR
    fonts_dir: Path = FONTS_DIR

    # game defaults
    default_team_size: int = 3
    min_team_size: int = 2
    max_team_size: int = 5
    max_players_total: int = 10
    default_target_score: int = 50
    sphere_hold_seconds: int = 10  # RP seconds awarded per round to holder

    # chaotic-zone effect cycling
    chaos_min_seconds: int = 60
    chaos_max_seconds: int = 300


def _parse_allowed_chats(raw: str) -> frozenset[int]:
    if not raw:
        return frozenset()
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            continue
    return frozenset(ids)


def load_settings() -> Settings:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Put it in .env or export it before running."
        )
    operate = _parse_allowed_chats(os.environ.get("OPERATE_CHAT_IDS", ""))
    allowed = _parse_allowed_chats(os.environ.get("ALLOWED_CHAT_IDS", ""))
    # Operate chats are always implicitly part of the access pool.
    allowed = allowed | operate
    return Settings(
        bot_token=token,
        database_url=os.environ.get("DATABASE_URL", "").strip(),
        operate_chat_ids=operate,
        allowed_chat_ids=allowed,
    )
