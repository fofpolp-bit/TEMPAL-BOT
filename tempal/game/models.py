"""Plain dataclass models that describe a Темпал match."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .abilities import Ability, get_ability
from .zones import Zone, ZoneId, get_zone


class GameStatus(str, Enum):
    LOBBY = "lobby"
    PLAYING = "playing"
    PAUSED = "paused"
    FINISHED = "finished"


class TeamId(str, Enum):
    A = "A"
    B = "B"


TEAM_EMOJI = {TeamId.A: "🅰️", TeamId.B: "🅱️"}
TEAM_LABEL = {TeamId.A: "Команда А", TeamId.B: "Команда Б"}


class ActionKind(str, Enum):
    MOVE_TO_SPHERE = "move"
    USE_ABILITY = "ability"
    ATTACK = "attack"
    PASS = "pass"


@dataclass
class LifetimeProfile:
    """Per-user career stats aggregated across every match they played.

    Lives in its own Postgres table, keyed by Telegram user_id. Updated once
    per finished match — see ``storage.accumulate_into_profile``.
    """

    user_id: int
    name: str = ""  # last seen display name; refreshed every aggregate
    matches_played: int = 0
    matches_won: int = 0
    total_personal_score: int = 0
    total_sphere_captures: int = 0
    total_nat20s: int = 0
    total_nat1s: int = 0
    total_nat10s: int = 0
    total_successful_freezes: int = 0
    total_successful_ability_uses: int = 0
    total_passes: int = 0
    total_times_fully_frozen: int = 0
    # Number of times each achievement_id was earned across matches.
    achievement_counts: dict[str, int] = field(default_factory=dict)
    last_updated: float = 0.0


@dataclass
class Player:
    user_id: int
    name: str
    team_id: Optional[TeamId] = None
    ability_id: Optional[str] = None  # references Ability.id; may be None per lore
    personal_score: int = 0
    is_spare: bool = False  # запасной игрок — не играет, пока не повышен

    # transient round flags
    frozen_rounds_left: int = 0  # skips next N rounds entirely
    bonuses_disabled_this_round: bool = False  # set by partial-freeze

    # stat counters for achievements
    nat20s: int = 0
    nat1s: int = 0
    nat10s: int = 0
    successful_freezes: int = 0
    successful_ability_uses: int = 0
    consecutive_sphere_captures: int = 0
    consecutive_full_successes: int = 0
    times_fully_frozen: int = 0
    pass_count: int = 0
    sphere_captures: int = 0  # total
    earned_achievements: list[str] = field(default_factory=list)

    def is_eligible(self) -> bool:
        """A player is eligible to play if they are on a team and not a spare."""
        return self.team_id is not None and not self.is_spare

    @property
    def ability(self) -> Optional[Ability]:
        return get_ability(self.ability_id) if self.ability_id else None


@dataclass
class Team:
    id: TeamId
    name: str
    emoji: str
    score: int = 0
    player_ids: list[int] = field(default_factory=list)


@dataclass
class PendingAction:
    """An action a player has chosen for the current round."""

    user_id: int
    kind: ActionKind
    target_user_id: Optional[int] = None  # for ability / attack
    note: Optional[str] = None  # free-form RP note


@dataclass
class RoundOutcome:
    """Resolved record of a single round, kept in the chronicle."""

    number: int
    zone_id: ZoneId
    arena_image: Optional[str]  # filename in arenas/ or None
    sphere_position: tuple[int, int]
    action_log: list[str]
    points_a: int
    points_b: int
    score_after: tuple[int, int]


@dataclass
class ChaosState:
    """Effect currently active inside the chaotic zone."""

    effect_label: str
    effect_emoji: str
    next_change_at: float  # epoch seconds


@dataclass
class Game:
    chat_id: int
    owner_id: int
    status: GameStatus = GameStatus.LOBBY
    team_size: int = 3
    target_score: int = 50

    teams: dict[TeamId, Team] = field(
        default_factory=lambda: {
            TeamId.A: Team(id=TeamId.A, name=TEAM_LABEL[TeamId.A], emoji=TEAM_EMOJI[TeamId.A]),
            TeamId.B: Team(id=TeamId.B, name=TEAM_LABEL[TeamId.B], emoji=TEAM_EMOJI[TeamId.B]),
        }
    )
    players: dict[int, Player] = field(default_factory=dict)
    # Reserves keyed by team — players who joined the lobby as «запас».
    reserves: dict[TeamId, list[int]] = field(
        default_factory=lambda: {TeamId.A: [], TeamId.B: []}
    )

    current_round: int = 0
    current_zone_id: ZoneId = ZoneId.NORMAL
    current_arena_image: Optional[str] = None
    sphere_position: tuple[int, int] = (3, 3)

    # tracks who last held the sphere — used for «married to sphere» streak
    last_sphere_holder_id: Optional[int] = None

    chaos: Optional[ChaosState] = None

    pending_actions: dict[int, PendingAction] = field(default_factory=dict)
    round_message_id: Optional[int] = None  # id of the "actions in progress" message

    # Free-form RP descriptions written by each player in the group chat
    # during the current round. Captured by the message handler.
    round_rp_notes: dict[int, str] = field(default_factory=dict)

    chronicle: list[RoundOutcome] = field(default_factory=list)
    created_at: float = 0.0
    finished_at: Optional[float] = None
    winner: Optional[TeamId] = None
    # Set to True after this match's stats have been folded into every
    # participant's LifetimeProfile, so /endgame can't double-count.
    profiles_recorded: bool = False

    # ── helpers ─────────────────────────────────────────────────────────
    @property
    def zone(self) -> Zone:
        return get_zone(self.current_zone_id)

    def add_player(self, user_id: int, name: str) -> Player:
        if user_id in self.players:
            return self.players[user_id]
        p = Player(user_id=user_id, name=name)
        self.players[user_id] = p
        return p

    def remove_player(self, user_id: int) -> bool:
        p = self.players.pop(user_id, None)
        if not p:
            return False
        if p.team_id is not None:
            self.teams[p.team_id].player_ids = [
                pid for pid in self.teams[p.team_id].player_ids if pid != user_id
            ]
            self.reserves[p.team_id] = [
                pid for pid in self.reserves[p.team_id] if pid != user_id
            ]
        return True

    def get_team(self, team_id: TeamId) -> Team:
        return self.teams[team_id]

    def eligible_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.is_eligible()]

    def players_in_team(self, team_id: TeamId) -> list[Player]:
        return [p for p in self.eligible_players() if p.team_id is team_id]

    def reserves_in_team(self, team_id: TeamId) -> list[Player]:
        return [p for p in self.players.values() if p.is_spare and p.team_id is team_id]

    def promote_reserve(self, team_id: TeamId) -> Optional[Player]:
        """Promote the first reserve of a team into the main roster."""
        for pid in list(self.reserves[team_id]):
            player = self.players.get(pid)
            if not player or not player.is_spare:
                continue
            player.is_spare = False
            self.reserves[team_id].remove(pid)
            if pid not in self.teams[team_id].player_ids:
                self.teams[team_id].player_ids.append(pid)
            return player
        return None
