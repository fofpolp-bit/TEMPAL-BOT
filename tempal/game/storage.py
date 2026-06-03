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
    LifetimeProfile,
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
    g.profiles_recorded = bool(data.get("profiles_recorded", False))
    return g


def _profile_from_dict(data: dict[str, Any]) -> LifetimeProfile:
    return LifetimeProfile(
        user_id=int(data["user_id"]),
        name=data.get("name", ""),
        matches_played=int(data.get("matches_played", 0)),
        matches_won=int(data.get("matches_won", 0)),
        total_personal_score=int(data.get("total_personal_score", 0)),
        total_sphere_captures=int(data.get("total_sphere_captures", 0)),
        total_nat20s=int(data.get("total_nat20s", 0)),
        total_nat1s=int(data.get("total_nat1s", 0)),
        total_nat10s=int(data.get("total_nat10s", 0)),
        total_successful_freezes=int(data.get("total_successful_freezes", 0)),
        total_successful_ability_uses=int(data.get("total_successful_ability_uses", 0)),
        total_passes=int(data.get("total_passes", 0)),
        total_times_fully_frozen=int(data.get("total_times_fully_frozen", 0)),
        achievement_counts={
            str(k): int(v) for k, v in (data.get("achievement_counts") or {}).items()
        },
        chat_ids=[int(c) for c in (data.get("chat_ids") or [])],
        last_updated=float(data.get("last_updated", 0.0)),
    )


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


def accumulate_into_profile(
    profile: LifetimeProfile,
    player: Player,
    *,
    is_winner: bool,
    when: float,
    chat_id: int | None = None,
) -> None:
    """Fold a single match's per-player counters into the lifetime profile.

    Always mutates ``profile`` in place — callers must persist the result.
    """
    profile.name = player.name or profile.name
    profile.matches_played += 1
    if is_winner:
        profile.matches_won += 1
    profile.total_personal_score += player.personal_score
    profile.total_sphere_captures += player.sphere_captures
    profile.total_nat20s += player.nat20s
    profile.total_nat1s += player.nat1s
    profile.total_nat10s += player.nat10s
    profile.total_successful_freezes += player.successful_freezes
    profile.total_successful_ability_uses += player.successful_ability_uses
    profile.total_passes += player.pass_count
    profile.total_times_fully_frozen += player.times_fully_frozen
    for ach_id in player.earned_achievements:
        profile.achievement_counts[ach_id] = (
            profile.achievement_counts.get(ach_id, 0) + 1
        )
    if chat_id is not None and chat_id not in profile.chat_ids:
        profile.chat_ids.append(chat_id)
    profile.last_updated = when


class JsonProfileStore:
    """Single-file JSON store for lifetime profiles (local dev / fallback)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._profiles: dict[int, LifetimeProfile] = {}
        self.load()

    def load(self) -> None:
        if not self._path.exists():
            self._profiles = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception(
                "Failed to read profiles file %s — starting empty", self._path
            )
            self._profiles = {}
            return
        self._profiles = {
            int(uid): _profile_from_dict(blob)
            for uid, blob in raw.get("profiles", {}).items()
        }

    def save(self) -> None:
        serialised = {
            "profiles": {
                str(uid): _to_jsonable(p) for uid, p in self._profiles.items()
            }
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent, prefix=".profiles-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(serialised, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            logger.exception("Failed to save profiles")
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def get(self, user_id: int) -> LifetimeProfile | None:
        return self._profiles.get(user_id)

    def put(self, profile: LifetimeProfile) -> None:
        self._profiles[profile.user_id] = profile
        self.save()

    def all_profiles(self) -> list[LifetimeProfile]:
        return list(self._profiles.values())


class PostgresProfileStore:
    """Postgres-backed lifetime profile store. One JSONB row per Telegram user.

    Designed to share connection semantics with PostgresGameStore — both stores
    are independent so reads/writes never interfere.
    """

    SCHEMA = """
        CREATE TABLE IF NOT EXISTS tempal_profiles (
            user_id     BIGINT PRIMARY KEY,
            data        JSONB NOT NULL,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """

    def __init__(self, dsn: str) -> None:
        import psycopg  # type: ignore[import-not-found]

        self._dsn = dsn
        self._psycopg = psycopg
        self._profiles: dict[int, LifetimeProfile] = {}
        self._ensure_schema()
        self.load()

    def _connect(self):
        return self._psycopg.connect(self._dsn, autocommit=True)

    def _ensure_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(self.SCHEMA)

    def load(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT user_id, data FROM tempal_profiles")
            rows = cur.fetchall()
        out: dict[int, LifetimeProfile] = {}
        for uid, blob in rows:
            try:
                out[int(uid)] = _profile_from_dict(blob)
            except Exception:
                logger.exception("Failed to deserialise profile for user %s", uid)
        self._profiles = out
        logger.info("Loaded %d lifetime profile(s) from Postgres", len(out))

    def _persist(self, user_id: int, profile: LifetimeProfile) -> None:
        from psycopg.types.json import Jsonb  # type: ignore[import-not-found]

        payload = _to_jsonable(profile)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tempal_profiles (user_id, data, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (user_id)
                DO UPDATE SET data = EXCLUDED.data, updated_at = now()
                """,
                (user_id, Jsonb(payload)),
            )

    def get(self, user_id: int) -> LifetimeProfile | None:
        return self._profiles.get(user_id)

    def put(self, profile: LifetimeProfile) -> None:
        self._profiles[profile.user_id] = profile
        try:
            self._persist(profile.user_id, profile)
        except Exception:
            logger.exception("Failed to persist profile to Postgres")

    def all_profiles(self) -> list[LifetimeProfile]:
        return list(self._profiles.values())


class JsonLastMatchStore:
    """Per-chat snapshot of the *last finished* match (JSON backend)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._games: dict[int, Game] = {}
        self.load()

    def load(self) -> None:
        if not self._path.exists():
            self._games = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception(
                "Failed to read last-match file %s — starting empty", self._path
            )
            self._games = {}
            return
        self._games = {
            int(cid): _game_from_dict(blob)
            for cid, blob in raw.get("games", {}).items()
        }

    def save(self) -> None:
        serialised = {
            "games": {
                str(cid): _to_jsonable(g) for cid, g in self._games.items()
            }
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent, prefix=".lastmatch-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(serialised, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            logger.exception("Failed to save last-match snapshot")
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def get(self, chat_id: int) -> Game | None:
        return self._games.get(chat_id)

    def put(self, game: Game) -> None:
        self._games[game.chat_id] = game
        self.save()


class PostgresLastMatchStore:
    """Postgres-backed per-chat last-finished-match snapshot."""

    SCHEMA = """
        CREATE TABLE IF NOT EXISTS tempal_last_match (
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
            cur.execute("SELECT chat_id, data FROM tempal_last_match")
            rows = cur.fetchall()
        out: dict[int, Game] = {}
        for chat_id, blob in rows:
            try:
                out[int(chat_id)] = _game_from_dict(blob)
            except Exception:
                logger.exception(
                    "Failed to deserialise last-match for chat %s", chat_id
                )
        self._games = out
        logger.info("Loaded %d last-match snapshot(s) from Postgres", len(out))

    def _persist(self, chat_id: int, game: Game) -> None:
        from psycopg.types.json import Jsonb  # type: ignore[import-not-found]

        payload = _to_jsonable(game)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tempal_last_match (chat_id, data, updated_at)
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
            logger.exception("Failed to persist last-match snapshot to Postgres")


def build_last_match_store(database_url: str, state_file: Path):
    if database_url:
        logger.info("Using Postgres last-match store")
        return PostgresLastMatchStore(database_url)
    path = state_file.with_name("last_match.json")
    logger.info("Using JSON last-match store at %s", path)
    return JsonLastMatchStore(path)


def build_profile_store(database_url: str, state_file: Path):
    """Mirror of ``build_store`` for the LifetimeProfile table."""
    if database_url:
        logger.info("Using Postgres profile store")
        return PostgresProfileStore(database_url)
    profile_path = state_file.with_name("profiles.json")
    logger.info("Using JSON profile store at %s", profile_path)
    return JsonProfileStore(profile_path)


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
ProfileStore = JsonProfileStore
LastMatchStore = JsonLastMatchStore


def record_profiles_for_match(store: "ProfileStore", game: Game) -> None:
    """Fold every eligible player's match stats into their lifetime profile.

    Idempotent — does nothing if ``game.profiles_recorded`` is already set.
    Mutates the flag on the passed game so callers should persist it afterwards.
    """
    import time as _time

    if game.profiles_recorded:
        return
    when = _time.time()
    for player in game.players.values():
        if player.is_spare or player.team_id is None:
            # Spares never actually played a match — don't pollute their lifetime.
            continue
        profile = store.get(player.user_id) or LifetimeProfile(user_id=player.user_id)
        accumulate_into_profile(
            profile,
            player,
            is_winner=(game.winner is not None and player.team_id == game.winner),
            when=when,
            chat_id=game.chat_id,
        )
        store.put(profile)
    game.profiles_recorded = True
