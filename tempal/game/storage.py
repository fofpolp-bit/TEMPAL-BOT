"""Persistence for in-flight games.

Supports two backends:
  - JSON file (single-file, atomic write) for local dev.
  - Postgres (one JSONB row per chat) for production / Neon.

The handler-facing API (`GameStore`) is identical for both backends so the
rest of the bot doesn't care which is active.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .models import (
    ChaosState,
    Game,
    GameStatus,
    PendingAction,
    Player,
    RoundOutcome,
    Team,
    TeamId,
)
from .zones import ZoneId

logger = logging.getLogger(__name__)


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        result: dict[str, Any] = {}
        for f in fields(obj):
            result[f.name] = _to_jsonable(getattr(obj, f.name))
        return result
    if isinstance(obj, dict):
        return {str(_to_jsonable(k)): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _player_from_dict(data: dict[str, Any]) -> Player:
    return Player(
        user_id=int(data["user_id"]),
        name=data["name"],
        team_id=TeamId(data["team_id"]) if data.get("team_id") else None,
        ability_id=data.get("ability_id"),
        personal_score=data.get("personal_score", 0),
        is_spare=data.get("is_spare", False),
        frozen_rounds_left=data.get("frozen_rounds_left", 0),
        bonuses_disabled_this_round=data.get("bonuses_disabled_this_round", False),
        nat20s=data.get("nat20s", 0),
        nat1s=data.get("nat1s", 0),
        nat10s=data.get("nat10s", 0),
        successful_freezes=data.get("successful_freezes", 0),
        successful_ability_uses=data.get("successful_ability_uses", 0),
        consecutive_sphere_captures=data.get("consecutive_sphere_captures", 0),
        consecutive_full_successes=data.get("consecutive_full_successes", 0),
        times_fully_frozen=data.get("times_fully_frozen", 0),
        pass_count=data.get("pass_count", 0),
        sphere_captures=data.get("sphere_captures", 0),
        earned_achievements=list(data.get("earned_achievements", [])),
    )


def _team_from_dict(data: dict[str, Any]) -> Team:
    return Team(
        id=TeamId(data["id"]),
        name=data["name"],
        emoji=data["emoji"],
        score=data.get("score", 0),
        player_ids=[int(x) for x in data.get("player_ids", [])],
    )


def _round_from_dict(data: dict[str, Any]) -> RoundOutcome:
    return RoundOutcome(
        number=data["number"],
        zone_id=ZoneId(data["zone_id"]),
        arena_image=data.get("arena_image"),
        sphere_position=tuple(data["sphere_position"]),  # type: ignore[arg-type]
        action_log=list(data.get("action_log", [])),
        points_a=data.get("points_a", 0),
        points_b=data.get("points_b", 0),
        score_after=tuple(data.get("score_after", (0, 0))),  # type: ignore[arg-type]
    )


def _chaos_from_dict(data: dict[str, Any] | None) -> ChaosState | None:
    if not data:
        return None
    return ChaosState(
        effect_label=data["effect_label"],
        effect_emoji=data["effect_emoji"],
        next_change_at=data["next_change_at"],
    )


def _pending_from_dict(data: dict[str, Any]) -> PendingAction:
    from .models import ActionKind

    return PendingAction(
        user_id=int(data["user_id"]),
        kind=ActionKind(data["kind"]),
        target_user_id=int(data["target_user_id"]) if data.get("target_user_id") else None,
        note=data.get("note"),
    )


def _game_from_dict(data: dict[str, Any]) -> Game:
    g = Game(chat_id=int(data["chat_id"]), owner_id=int(data["owner_id"]))
    g.status = GameStatus(data.get("status", "lobby"))
    g.team_size = data.get("team_size", 3)
    g.target_score = data.get("target_score", 50)
    g.teams = {TeamId(k): _team_from_dict(v) for k, v in data.get("teams", {}).items()}
    g.players = {int(k): _player_from_dict(v) for k, v in data.get("players", {}).items()}
    g.reserves = {
        TeamId(k): [int(x) for x in v]
        for k, v in data.get("reserves", {}).items()
    } or {TeamId.A: [], TeamId.B: []}
    g.current_round = data.get("current_round", 0)
    g.current_zone_id = ZoneId(data.get("current_zone_id", "normal"))
    g.current_arena_image = data.get("current_arena_image")
    g.sphere_position = tuple(data.get("sphere_position", (3, 3)))  # type: ignore[assignment]
    g.last_sphere_holder_id = data.get("last_sphere_holder_id")
    g.chaos = _chaos_from_dict(data.get("chaos"))
    g.pending_actions = {
        int(k): _pending_from_dict(v) for k, v in data.get("pending_actions", {}).items()
    }
    g.round_message_id = data.get("round_message_id")
    g.round_rp_notes = {
        int(k): v for k, v in data.get("round_rp_notes", {}).items()
    }
    g.chronicle = [_round_from_dict(r) for r in data.get("chronicle", [])]
    g.created_at = data.get("created_at", 0.0)
    g.finished_at = data.get("finished_at")
    g.winner = TeamId(data["winner"]) if data.get("winner") else None
    return g


class JsonGameStore:
    """Single-file JSON store. All operations are synchronous + small."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._games: dict[int, Game] = {}
        self.load()

    # ── disk ─────────────────────────────────────────────────────────────
    def load(self) -> None:
        if not self._path.exists():
            self._games = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to read state file %s — starting empty", self._path)
            self._games = {}
            return
        self._games = {
            int(chat_id): _game_from_dict(blob) for chat_id, blob in raw.get("games", {}).items()
        }

    def save(self) -> None:
        serialised = {
            "games": {
                str(chat_id): _to_jsonable(game) for chat_id, game in self._games.items()
            }
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # atomic write
        fd, tmp_path = tempfile.mkstemp(dir=self._path.parent, prefix=".games-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(serialised, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            logger.exception("Failed to save state")
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    # ── api ──────────────────────────────────────────────────────────────
    def get(self, chat_id: int) -> Game | None:
        return self._games.get(chat_id)

    def put(self, game: Game) -> None:
        self._games[game.chat_id] = game
        self.save()

    def delete(self, chat_id: int) -> None:
        self._games.pop(chat_id, None)
        self.save()

    def all_games(self) -> list[Game]:
        return list(self._games.values())


class PostgresGameStore:
    """Postgres-backed store. One JSONB row per chat_id.

    Read-through cache + write-through to Postgres. The cache is hydrated
    once on startup; subsequent writes always hit the DB synchronously so a
    crash or container restart loses nothing.
    """

    SCHEMA = """
        CREATE TABLE IF NOT EXISTS tempal_games (
            chat_id     BIGINT PRIMARY KEY,
            data        JSONB NOT NULL,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """

    def __init__(self, dsn: str) -> None:
        import psycopg  # type: ignore[import-not-found]

        self._dsn = dsn
        self._psycopg = psycopg
        self._games: dict[int, Game] = {}
        self._ensure_schema()
        self.load()

    def _connect(self):
        return self._psycopg.connect(self._dsn, autocommit=True)

    def _ensure_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(self.SCHEMA)

    def load(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT chat_id, data FROM tempal_games")
            rows = cur.fetchall()
        out: dict[int, Game] = {}
        for chat_id, blob in rows:
            try:
                out[int(chat_id)] = _game_from_dict(blob)
            except Exception:
                logger.exception("Failed to deserialise game for chat %s", chat_id)
        self._games = out
        logger.info("Loaded %d game(s) from Postgres", len(out))

    def _persist(self, chat_id: int, game: Game) -> None:
        from psycopg.types.json import Jsonb  # type: ignore[import-not-found]

        payload = _to_jsonable(game)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tempal_games (chat_id, data, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (chat_id)
                DO UPDATE SET data = EXCLUDED.data, updated_at = now()
                """,
                (chat_id, Jsonb(payload)),
            )

    def get(self, chat_id: int) -> Game | None:
        return self._games.get(chat_id)

    def put(self, game: Game) -> None:
        self._games[game.chat_id] = game
        try:
            self._persist(game.chat_id, game)
        except Exception:
            logger.exception("Failed to persist game to Postgres")

    def delete(self, chat_id: int) -> None:
        self._games.pop(chat_id, None)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM tempal_games WHERE chat_id = %s", (chat_id,))
        except Exception:
            logger.exception("Failed to delete game from Postgres")

    def all_games(self) -> list[Game]:
        return list(self._games.values())


def build_store(database_url: str, state_file: Path):
    """Pick the right backend based on whether DATABASE_URL is set."""
    if database_url:
        logger.info("Using Postgres game store")
        store = PostgresGameStore(database_url)
        # One-time migration: if Postgres is empty and a JSON file exists
        # locally, slurp it in so we don't lose state on first deploy.
        if not store.all_games() and state_file.exists():
            try:
                fallback = JsonGameStore(state_file)
                migrated = 0
                for game in fallback.all_games():
                    store.put(game)
                    migrated += 1
                if migrated:
                    logger.info(
                        "Migrated %d game(s) from JSON file to Postgres", migrated
                    )
            except Exception:
                logger.exception("JSON-to-Postgres migration failed")
        return store
    logger.info("Using JSON game store at %s", state_file)
    return JsonGameStore(state_file)


# Backwards-compat alias — keep `GameStore` name for callers that imported it.
GameStore = JsonGameStore
