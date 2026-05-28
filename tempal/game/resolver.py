"""Resolves a Темпал round.

Order of operations within a round:
    1. Apply «frozen» status: players with ``frozen_rounds_left > 0`` skip.
    2. Everyone rolls a d20 with zone modifiers.
    3. Process ability casts in cast-order — bonus/debuff strength depends on
       the caster's own roll (per lore).
    4. Process attacks: if attacker's roll > target's roll, target's action is
       disrupted (sphere chase fails, ability fizzles, attack cancelled).
    5. Score the sphere: whichever team has the surviving best MOVE_TO_SPHERE
       roll holds the sphere — partial success = 5 points, full success = 10
       points (1 point ≈ 1 RP-second of holding per lore).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from .abilities import Ability, TargetType, get_ability
from .achievements import ACHIEVEMENTS
from .models import (
    ActionKind,
    Game,
    PendingAction,
    Player,
    RoundOutcome,
    TEAM_LABEL,
    TeamId,
)
from .rolls import RollOutcome, RollResult, roll_d20
from .zones import ZoneId


# Points awarded by the lore: 10 RP-seconds of holding = 10 points.
POINTS_FULL_HOLD = 10
POINTS_PARTIAL_HOLD = 5


@dataclass
class PlayerActionReport:
    """Per-player resolved report for a round."""

    player_id: int
    player_name: str
    team_id: TeamId
    ability: Optional[Ability]
    action: PendingAction
    roll: Optional[RollResult]
    success_summary: str
    target_id: Optional[int] = None
    target_name: Optional[str] = None
    disrupted: bool = False  # set if an attack disrupted this player's action


@dataclass
class RoundResolution:
    """Everything the chat needs to know about a finished round."""

    round_number: int
    zone_id: ZoneId
    sphere_position: tuple[int, int]
    reports: list[PlayerActionReport]
    points_a: int
    points_b: int
    chronicle_entry: RoundOutcome
    new_achievements: list[tuple[int, str]] = field(default_factory=list)
    winner: Optional[TeamId] = None


def _other_team(team_id: TeamId) -> TeamId:
    return TeamId.B if team_id is TeamId.A else TeamId.A


def _outcome_label(outcome: RollOutcome) -> str:
    return {
        RollOutcome.SUCCESS: "✅ Полный успех",
        RollOutcome.PARTIAL: "🟡 Частичный успех",
        RollOutcome.FAIL: "❌ Неудача",
    }[outcome]


def _zone_modifiers(game: Game) -> list[tuple[str, int]]:
    """Baseline d20 modifiers from the current zone.

    Per lore, acceleration speeds you up (we encode as +1), slowdown as −1.
    Normal & chaos have no static modifier; inversion is handled by mirroring
    the natural roll (see resolver loop).
    """
    z = game.zone
    if z.id is ZoneId.ACCELERATION:
        return [(f"{z.emoji} зона (+1)", 1)]
    if z.id is ZoneId.SLOWDOWN:
        return [(f"{z.emoji} зона (−1)", -1)]
    return []


def resolve_round(
    game: Game,
    *,
    rng: random.Random | None = None,
) -> RoundResolution:
    """Resolve the current round and update ``game`` in place."""
    rng = rng or random
    eligible = list(game.eligible_players())
    actions = dict(game.pending_actions)

    # Reset per-round transient flags
    for p in eligible:
        p.bonuses_disabled_this_round = False

    # Frozen players are auto-passed and their frozen counter ticks down
    for p in eligible:
        if p.frozen_rounds_left > 0:
            p.frozen_rounds_left -= 1
            actions[p.user_id] = PendingAction(user_id=p.user_id, kind=ActionKind.PASS)
        elif p.user_id not in actions:
            actions[p.user_id] = PendingAction(user_id=p.user_id, kind=ActionKind.PASS)

    # ── Phase 1: roll a d20 for every player who has a real action ────────
    zone_mods = _zone_modifiers(game)
    rolls: dict[int, RollResult] = {}
    for p in eligible:
        action = actions[p.user_id]
        if action.kind is ActionKind.PASS:
            continue
        rolls[p.user_id] = roll_d20(modifiers=list(zone_mods), rng=rng)

    # ── Phase 2: process ability casts in deterministic order ─────────────
    # Each successful cast modifies the target's stored RollResult.
    cast_log: dict[int, str] = {}  # caster_id → flavour line
    for caster_id in sorted(rolls.keys()):
        action = actions[caster_id]
        caster = game.players[caster_id]
        if action.kind is not ActionKind.USE_ABILITY:
            continue
        ability = caster.ability
        if ability is None:
            continue
        cast_roll = rolls[caster_id]
        target_id = action.target_user_id
        target = game.players.get(target_id) if target_id else None

        if cast_roll.outcome is RollOutcome.FAIL:
            cast_log[caster_id] = f"{ability.emoji} {ability.name} не сработала."
            continue
        if target is None and ability.target is not TargetType.NONE:
            cast_log[caster_id] = f"{ability.emoji} {ability.name} — некому."
            continue

        if ability.id == "acceleration":
            assert target is not None
            value = 4 if cast_roll.outcome is RollOutcome.SUCCESS else 1
            _apply_modifier(rolls, target.user_id, (f"{ability.emoji} +{value}", value))
            cast_log[caster_id] = f"{ability.emoji} ускоряет {target.name} (+{value})"

        elif ability.id == "slowdown":
            assert target is not None
            value = -4 if cast_roll.outcome is RollOutcome.SUCCESS else -1
            _apply_modifier(rolls, target.user_id, (f"{ability.emoji} {value}", value))
            cast_log[caster_id] = f"{ability.emoji} замедляет {target.name} ({value})"

        elif ability.id == "freeze":
            assert target is not None
            if cast_roll.outcome is RollOutcome.SUCCESS:
                target.frozen_rounds_left = max(target.frozen_rounds_left, 1)
                # Also nullify the target's action this round
                _disrupt(rolls, target.user_id, hard=True)
                cast_log[caster_id] = (
                    f"{ability.emoji} замораживает {target.name} — пропустит следующий раунд"
                )
            else:
                # partial freeze: strip the target's bonuses this round
                target.bonuses_disabled_this_round = True
                _strip_bonuses(rolls, target.user_id)
                cast_log[caster_id] = (
                    f"{ability.emoji} срывает все бонусы с {target.name}"
                )

        elif ability.id == "inversion":
            assert target is not None
            if target.user_id in rolls:
                tr = rolls[target.user_id]
                inverted = 21 - tr.raw
                new_mods = list(tr.modifiers)
                if cast_roll.outcome is RollOutcome.SUCCESS:
                    # flip the SIGN of every existing modifier
                    new_mods = [(label, -value) for label, value in tr.modifiers]
                new_final = max(1, min(30, inverted + sum(v for _, v in new_mods)))
                rolls[target.user_id] = RollResult(
                    raw=inverted,
                    modifiers=new_mods + [(f"{ability.emoji} инверсия", 0)],
                    final=new_final,
                    outcome=_classify_after_invert(new_final),
                )
                cast_log[caster_id] = (
                    f"{ability.emoji} переворачивает ролл {target.name}"
                )
            else:
                cast_log[caster_id] = f"{ability.emoji} {ability.name} — у цели нет действия."

    # Re-classify every roll after ability modifications
    for uid, rr in rolls.items():
        rr.outcome = _classify_after_invert(rr.final)

    # ── Phase 3: process attacks ─────────────────────────────────────────
    # Attack disrupts target if attacker's roll ≥ target's roll.
    attack_log: dict[int, str] = {}
    for attacker_id in sorted(rolls.keys()):
        action = actions[attacker_id]
        if action.kind is not ActionKind.ATTACK:
            continue
        attacker_roll = rolls[attacker_id]
        target_id = action.target_user_id
        target = game.players.get(target_id) if target_id else None
        if target is None:
            attack_log[attacker_id] = "⚔️ Атака без цели — мимо."
            continue
        target_roll = rolls.get(target.user_id)
        if attacker_roll.outcome is RollOutcome.FAIL:
            attack_log[attacker_id] = f"⚔️ Атака на {target.name} провалена."
            continue
        if target_roll is None:
            # target wasn't doing anything (PASS / frozen) — attack lands trivially
            attack_log[attacker_id] = f"⚔️ Атака на {target.name} попадает (тот пасовал)."
            continue
        if attacker_roll.final >= target_roll.final:
            _disrupt(rolls, target.user_id, hard=False)
            attack_log[attacker_id] = (
                f"⚔️ Атака на {target.name} срывает их ход!"
            )
        else:
            attack_log[attacker_id] = (
                f"⚔️ Атака на {target.name} мимо — тот оказался быстрее."
            )

    # ── Phase 4: assemble per-player reports ─────────────────────────────
    reports: list[PlayerActionReport] = []
    for p in eligible:
        action = actions[p.user_id]
        roll = rolls.get(p.user_id)
        target_id = action.target_user_id
        target_name = game.players[target_id].name if target_id in game.players else None
        summary = _describe(p, action, roll, target_name, cast_log, attack_log)
        reports.append(
            PlayerActionReport(
                player_id=p.user_id,
                player_name=p.name,
                team_id=p.team_id,  # type: ignore[arg-type]
                ability=p.ability,
                action=action,
                roll=roll,
                target_id=target_id,
                target_name=target_name,
                success_summary=summary,
                disrupted=(roll is not None and roll.outcome is RollOutcome.FAIL
                           and "сорван" in summary.lower()),
            )
        )

    # ── Phase 5: award sphere points ─────────────────────────────────────
    sphere_winner_team, points_a, points_b, sphere_holder_id = _award_sphere_points(
        reports, game
    )

    # ── Phase 6: book-keeping + achievements ─────────────────────────────
    new_achievements = _award_achievements(
        game, reports, sphere_holder_id=sphere_holder_id
    )

    chronicle = RoundOutcome(
        number=game.current_round,
        zone_id=game.current_zone_id,
        arena_image=game.current_arena_image,
        sphere_position=game.sphere_position,
        action_log=[r.success_summary for r in reports],
        points_a=points_a,
        points_b=points_b,
        score_after=(game.teams[TeamId.A].score, game.teams[TeamId.B].score),
    )
    game.chronicle.append(chronicle)

    winner: Optional[TeamId] = None
    if game.teams[TeamId.A].score >= game.target_score:
        winner = TeamId.A
    elif game.teams[TeamId.B].score >= game.target_score:
        winner = TeamId.B

    return RoundResolution(
        round_number=game.current_round,
        zone_id=game.current_zone_id,
        sphere_position=game.sphere_position,
        reports=reports,
        points_a=points_a,
        points_b=points_b,
        chronicle_entry=chronicle,
        new_achievements=new_achievements,
        winner=winner,
    )


# ── helpers ────────────────────────────────────────────────────────────────


def _classify_after_invert(final: int) -> RollOutcome:
    if final <= 5:
        return RollOutcome.FAIL
    if final <= 15:
        return RollOutcome.PARTIAL
    return RollOutcome.SUCCESS


def _apply_modifier(
    rolls: dict[int, RollResult], target_id: int, mod: tuple[str, int]
) -> None:
    tr = rolls.get(target_id)
    if tr is None:
        return
    new_mods = list(tr.modifiers) + [mod]
    new_final = max(1, min(30, tr.raw + sum(v for _, v in new_mods)))
    rolls[target_id] = RollResult(
        raw=tr.raw,
        modifiers=new_mods,
        final=new_final,
        outcome=_classify_after_invert(new_final),
    )


def _strip_bonuses(rolls: dict[int, RollResult], target_id: int) -> None:
    """Remove all positive modifiers from a target's roll (partial freeze)."""
    tr = rolls.get(target_id)
    if tr is None:
        return
    kept = [(label, v) for label, v in tr.modifiers if v <= 0]
    new_final = max(1, min(30, tr.raw + sum(v for _, v in kept)))
    rolls[target_id] = RollResult(
        raw=tr.raw,
        modifiers=kept,
        final=new_final,
        outcome=_classify_after_invert(new_final),
    )


def _disrupt(rolls: dict[int, RollResult], target_id: int, *, hard: bool) -> None:
    """Mark a target's action as disrupted by attack/freeze.

    We model this by collapsing their final roll to 1 — i.e. failure tier — so
    downstream scoring naturally skips them.
    """
    tr = rolls.get(target_id)
    if tr is None:
        return
    rolls[target_id] = RollResult(
        raw=tr.raw,
        modifiers=tr.modifiers + [("✋ сорвано", 0)],
        final=1,
        outcome=RollOutcome.FAIL,
    )


def _describe(
    player: Player,
    action: PendingAction,
    roll: Optional[RollResult],
    target_name: Optional[str],
    cast_log: dict[int, str],
    attack_log: dict[int, str],
) -> str:
    head = f"<b>{player.name}</b>"
    if roll is None:
        if action.kind is ActionKind.PASS and player.frozen_rounds_left > 0:
            return f"❄️ {head} пропускает ход — заморожен(а)."
        if action.kind is ActionKind.PASS:
            return f"⏸ {head} пасует."
        return f"{head} — нет действия."

    roll_part = f"🎲 <b>{roll.final}</b>"
    if roll.modifiers:
        mod_str = ", ".join(label for label, _ in roll.modifiers)
        roll_part = f"🎲 {roll.raw} → <b>{roll.final}</b> ({mod_str})"
    outcome_part = _outcome_label(roll.outcome)

    if action.kind is ActionKind.MOVE_TO_SPHERE:
        return f"🏃 {head} рвётся к сфере. {roll_part} · {outcome_part}"
    if action.kind is ActionKind.ATTACK:
        tgt = target_name or "противника"
        line = f"⚔️ {head} атакует <b>{tgt}</b>. {roll_part} · {outcome_part}"
        extra = attack_log.get(player.user_id)
        if extra:
            line += f"\n   ↳ {extra}"
        return line
    if action.kind is ActionKind.USE_ABILITY and player.ability:
        ab = player.ability
        tgt = f" на <b>{target_name}</b>" if target_name else ""
        line = f"{ab.emoji} {head} применяет «{ab.name}»{tgt}. {roll_part} · {outcome_part}"
        extra = cast_log.get(player.user_id)
        if extra:
            line += f"\n   ↳ {extra}"
        return line
    return f"{head} {outcome_part}"


def _award_sphere_points(
    reports: list[PlayerActionReport],
    game: Game,
) -> tuple[Optional[TeamId], int, int, Optional[int]]:
    """Find the player who captured the sphere this round and credit their team.

    Per lore, the sphere is held by whoever rolls best on MOVE_TO_SPHERE.
    Full success → 10 points (10 RP-seconds). Partial → 5 points (~5 sec).
    Failure or tie at the top → nobody scores.
    """
    contenders = [
        r
        for r in reports
        if r.action.kind is ActionKind.MOVE_TO_SPHERE
        and r.roll is not None
        and r.roll.outcome is not RollOutcome.FAIL
    ]
    if not contenders:
        return None, 0, 0, None

    contenders.sort(key=lambda r: r.roll.final, reverse=True)  # type: ignore[union-attr]
    top = contenders[0]
    # tie at the top → no one holds the sphere
    if len(contenders) > 1 and contenders[1].roll.final == top.roll.final:  # type: ignore[union-attr]
        return None, 0, 0, None

    points = (
        POINTS_FULL_HOLD
        if top.roll.outcome is RollOutcome.SUCCESS  # type: ignore[union-attr]
        else POINTS_PARTIAL_HOLD
    )
    if top.team_id is TeamId.A:
        game.teams[TeamId.A].score += points
        return TeamId.A, points, 0, top.player_id
    game.teams[TeamId.B].score += points
    return TeamId.B, 0, points, top.player_id


def _award_achievements(
    game: Game,
    reports: list[PlayerActionReport],
    *,
    sphere_holder_id: Optional[int],
) -> list[tuple[int, str]]:
    awarded: list[tuple[int, str]] = []

    def grant(p: Player, ach_id: str) -> None:
        if ach_id in p.earned_achievements:
            return
        if ach_id not in ACHIEVEMENTS:
            return
        p.earned_achievements.append(ach_id)
        awarded.append((p.user_id, ach_id))

    # ── per-player stat updates ─────────────────────────────────────────
    for r in reports:
        p = game.players.get(r.player_id)
        if not p:
            continue

        if r.action.kind is ActionKind.PASS:
            p.pass_count += 1
            if p.pass_count >= 3:
                grant(p, "ya_ne_pasuyu")

        if r.roll is not None:
            # natural-roll achievements use the raw die value before mods
            if r.roll.raw == 20:
                p.nat20s += 1
                if p.nat20s >= 3:
                    grant(p, "vetorio_v_shoke")
            if r.roll.raw == 1:
                p.nat1s += 1
                if p.nat1s >= 3:
                    grant(p, "kubik_protiv_menya")
            if r.roll.raw == 10:
                p.nat10s += 1
                if p.nat10s == 3:
                    grant(p, "kalkulyator")
            if r.roll.outcome is RollOutcome.SUCCESS:
                p.consecutive_full_successes += 1
                if p.consecutive_full_successes >= 3:
                    grant(p, "tachion_v_kofeyne")
            else:
                p.consecutive_full_successes = 0

        # successful ability uses
        if (
            r.action.kind is ActionKind.USE_ABILITY
            and r.roll is not None
            and r.roll.outcome is not RollOutcome.FAIL
        ):
            p.successful_ability_uses += 1
            if p.successful_ability_uses >= 4:
                grant(p, "temporal_expert")
            if (
                p.ability is not None
                and p.ability.id == "freeze"
                and r.roll.outcome is RollOutcome.SUCCESS
            ):
                p.successful_freezes += 1
                if p.successful_freezes >= 3:
                    grant(p, "shavermoverum")

    # Full-freeze accounting (player got their next round skipped)
    for p in game.eligible_players():
        if p.frozen_rounds_left > 0 and "menya_tut_ne_bylo" not in p.earned_achievements:
            p.times_fully_frozen += 1
            if p.times_fully_frozen >= 2:
                grant(p, "menya_tut_ne_bylo")

    # «Замуж за сферу» — three rounds in a row holding sphere
    for p in game.eligible_players():
        if p.user_id == sphere_holder_id:
            p.consecutive_sphere_captures += 1
            p.sphere_captures += 1
            if p.consecutive_sphere_captures >= 3:
                grant(p, "married_to_sphere")
        else:
            p.consecutive_sphere_captures = 0
    game.last_sphere_holder_id = sphere_holder_id

    # «Зерохиро» — team came from behind to victory.
    # We'll compute this only on final round (caller checks); placeholder here:
    if game.teams[TeamId.A].score >= game.target_score or game.teams[TeamId.B].score >= game.target_score:
        winner_team = (
            TeamId.A if game.teams[TeamId.A].score >= game.target_score else TeamId.B
        )
        loser_team = _other_team(winner_team)
        # If at any point losing team was ahead by >= half of target_score, give zerohero
        for past in game.chronicle:
            score_a, score_b = past.score_after
            winning_score = score_a if winner_team is TeamId.A else score_b
            losing_score = score_a if loser_team is TeamId.A else score_b
            if losing_score - winning_score >= game.target_score // 2:
                for p in game.players_in_team(winner_team):
                    grant(p, "zerohero")
                break

    return awarded
