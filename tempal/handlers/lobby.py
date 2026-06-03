"""Lobby commands: creating a new game, joining, picking teams, starting."""

from __future__ import annotations

import logging
import random
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message

from ..game.models import Game, GameStatus, TEAM_LABEL, TeamId
from ..game.rolls import assign_random_abilities
from ..game.zones import non_chaos_zones
from ..services.arenas import arena_display_name, pick_random_arena
from .common import (
    BotContext,
    fmt_player_name,
    format_scoreboard,
    get_context,
    is_group_owner_or_admin,
)
from .keyboards import TARGET_SCORE_OPTIONS, lobby_keyboard

logger = logging.getLogger(__name__)
router = Router(name="lobby")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "<b>Темпал</b> — командная игра во времени.\n\n"
        "Добавь меня в групповой чат и напиши /newgame, чтобы открыть лобби.\n"
        "Подробности — /help.",
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "🕰️ <b>Команды бота</b>\n\n"
        "<b>Лобби и матч</b>\n"
        "• /newgame — открыть лобби (только в группе)\n"
        "• /join — войти в лобби\n"
        "• /leave — выйти из лобби\n"
        "• /myteam A|B — выбрать команду вручную\n"
        "• /spare [A|B] — стать запасным\n"
        "• /shuffle — пересобрать команды (хозяин)\n"
        "• /startgame — стартануть матч\n"
        "• /act — меню действий в свой ход\n\n"
        "<b>Инфо</b>\n"
        "• /status — состояние раунда\n"
        "• /score — счёт\n"
        "• /zone — текущая зона арены\n"
        "• /card [reply] — карточка матча\n"
        "• /profile [reply] — лайфтайм-карточка игрока\n"
        "• /top [score|bronzes] — топ-10 чата\n"
        "• /history — последний завершённый матч\n"
        "• /coinflip — рандомно A или B\n\n"
        "<b>Управление (хозяин лобби / владелец группы)</b>\n"
        "• /pause /resume — пауза и продолжение\n"
        "• /skip — переобъявить текущий раунд\n"
        "• /kick [reply] — выгнать игрока из лобби\n"
        "• /forcepass [reply] — форсить пас зависшему игроку\n"
        "• /endgame — закончить матч",
        parse_mode="HTML",
    )


@router.message(Command("newgame"))
async def cmd_newgame(message: Message, bot: Bot, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Темпал — командная игра. Зови меня в групповой чат и зови сюда /newgame.")
        return
    existing = ctx.store.get(message.chat.id)
    if existing and existing.status not in {GameStatus.FINISHED}:
        await message.answer(
            "Здесь уже идёт матч. Заверши его /endgame, чтобы открыть новое лобби."
        )
        return

    game = Game(
        chat_id=message.chat.id,
        owner_id=message.from_user.id,
        team_size=ctx.settings.default_team_size,
        target_score=ctx.settings.default_target_score,
        created_at=time.time(),
    )
    ctx.store.put(game)

    await _send_lobby(message, game)


async def _send_lobby(message: Message, game: Game) -> None:
    text = _lobby_text(game)
    await message.answer(text, parse_mode="HTML", reply_markup=lobby_keyboard(game))


def _lobby_text(game: Game) -> str:
    def team_block(team_id: TeamId) -> str:
        team = game.teams[team_id]
        players = [
            game.players[pid] for pid in team.player_ids if pid in game.players
        ]
        body = (
            "\n".join(f"• {fmt_player_name(p.name)}" for p in players)
            if players
            else "<i>(пусто)</i>"
        )
        reserves = game.reserves_in_team(team_id)
        reserve_body = (
            "\n".join(f"🪑 {fmt_player_name(p.name)}" for p in reserves)
            if reserves
            else ""
        )
        return f"{team.emoji} <b>{team.name}</b>\n{body}" + (
            f"\n{reserve_body}" if reserve_body else ""
        )

    unassigned = [
        p for p in game.players.values() if p.team_id is None and not p.is_spare
    ]
    unassigned_block = (
        "\n".join(f"• {fmt_player_name(p.name)}" for p in unassigned)
        if unassigned
        else "<i>(никого)</i>"
    )

    return (
        "🕰️ <b>Лобби Темпал</b>\n"
        f"Размер команды: <b>{game.team_size}</b> (мин 2, макс 5)\n"
        f"До победы: <b>{game.target_score}</b> очков\n\n"
        f"{team_block(TeamId.A)}\n\n"
        f"{team_block(TeamId.B)}\n\n"
        f"<b>Без команды:</b>\n{unassigned_block}\n\n"
        "🪑 — запасной игрок: не играет, но получает способность и заменит "
        "выбывшего из своей команды."
    )


# ── callbacks ─────────────────────────────────────────────────────────


@router.callback_query(F.data == "lobby:join")
async def cb_join(call: CallbackQuery, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(call.message.chat.id)
    if not game or game.status is not GameStatus.LOBBY:
        await call.answer("Лобби уже закрыто", show_alert=True)
        return
    if call.from_user.id in game.players:
        await call.answer("Ты уже в лобби")
        return
    if len(game.players) >= ctx.settings.max_players_total:
        await call.answer("Максимум 10 игроков", show_alert=True)
        return
    game.add_player(call.from_user.id, call.from_user.full_name)
    ctx.store.put(game)
    await call.message.edit_text(
        _lobby_text(game), parse_mode="HTML", reply_markup=lobby_keyboard(game)
    )
    await call.answer("Ты в лобби")


@router.callback_query(F.data == "lobby:leave")
async def cb_leave(call: CallbackQuery, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(call.message.chat.id)
    if not game or game.status is not GameStatus.LOBBY:
        await call.answer("Лобби уже закрыто", show_alert=True)
        return
    if call.from_user.id not in game.players:
        await call.answer("Тебя и так нет")
        return
    game.remove_player(call.from_user.id)
    ctx.store.put(game)
    await call.message.edit_text(
        _lobby_text(game), parse_mode="HTML", reply_markup=lobby_keyboard(game)
    )
    await call.answer("Ушёл из лобби")


@router.callback_query(F.data.startswith("lobby:team:"))
async def cb_team(call: CallbackQuery, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(call.message.chat.id)
    if not game or game.status is not GameStatus.LOBBY:
        await call.answer("Лобби уже закрыто", show_alert=True)
        return
    team_str = call.data.split(":")[-1]
    team_id = TeamId(team_str)
    player = game.players.get(call.from_user.id) or game.add_player(
        call.from_user.id, call.from_user.full_name
    )
    if (
        len(game.teams[team_id].player_ids) >= ctx.settings.max_team_size
        and player.user_id not in game.teams[team_id].player_ids
    ):
        await call.answer(f"В команде {team_id.value} уже максимум", show_alert=True)
        return
    # move out of previous team (and reserves)
    for tid in (TeamId.A, TeamId.B):
        game.teams[tid].player_ids = [
            pid for pid in game.teams[tid].player_ids if pid != player.user_id
        ]
        game.reserves[tid] = [
            pid for pid in game.reserves[tid] if pid != player.user_id
        ]
    player.team_id = team_id
    player.is_spare = False
    if player.user_id not in game.teams[team_id].player_ids:
        game.teams[team_id].player_ids.append(player.user_id)
    ctx.store.put(game)
    await call.message.edit_text(
        _lobby_text(game), parse_mode="HTML", reply_markup=lobby_keyboard(game)
    )
    await call.answer(f"Ты в команде {team_id.value}")


@router.callback_query(F.data.startswith("lobby:spare:"))
async def cb_spare(call: CallbackQuery, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(call.message.chat.id)
    if not game or game.status is not GameStatus.LOBBY:
        await call.answer("Лобби уже закрыто", show_alert=True)
        return
    team_id = TeamId(call.data.split(":")[-1])
    player = game.players.get(call.from_user.id) or game.add_player(
        call.from_user.id, call.from_user.full_name
    )
    # move out of main roster of any team
    for tid in (TeamId.A, TeamId.B):
        game.teams[tid].player_ids = [
            pid for pid in game.teams[tid].player_ids if pid != player.user_id
        ]
        game.reserves[tid] = [
            pid for pid in game.reserves[tid] if pid != player.user_id
        ]
    player.team_id = team_id
    player.is_spare = True
    game.reserves[team_id].append(player.user_id)
    ctx.store.put(game)
    await call.message.edit_text(
        _lobby_text(game), parse_mode="HTML", reply_markup=lobby_keyboard(game)
    )
    await call.answer(f"Ты в запасе {team_id.value}")


@router.callback_query(F.data == "lobby:autoteams")
async def cb_autoteams(call: CallbackQuery, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(call.message.chat.id)
    if not game or game.status is not GameStatus.LOBBY:
        await call.answer("Лобби уже закрыто", show_alert=True)
        return
    if call.from_user.id != game.owner_id:
        await call.answer("Только создатель лобби может пересобрать команды.", show_alert=True)
        return
    _shuffle_teams(game)
    ctx.store.put(game)
    await call.message.edit_text(
        _lobby_text(game), parse_mode="HTML", reply_markup=lobby_keyboard(game)
    )
    await call.answer("Команды перетасованы")


def _shuffle_teams(game: Game) -> None:
    ids = list(game.players.keys())
    random.shuffle(ids)
    for team in game.teams.values():
        team.player_ids = []
    for idx, pid in enumerate(ids):
        team_id = TeamId.A if idx % 2 == 0 else TeamId.B
        game.players[pid].team_id = team_id
        game.players[pid].is_spare = False
        game.teams[team_id].player_ids.append(pid)


@router.callback_query(F.data.startswith("lobby:target:"))
async def cb_target(call: CallbackQuery, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(call.message.chat.id)
    if not game or game.status is not GameStatus.LOBBY:
        await call.answer("Лобби уже закрыто", show_alert=True)
        return
    if call.from_user.id != game.owner_id:
        await call.answer(
            "Только создатель лобби может менять цель по очкам.", show_alert=True
        )
        return
    try:
        score = int(call.data.split(":")[-1])
    except ValueError:
        await call.answer("Неверное значение", show_alert=True)
        return
    if score not in TARGET_SCORE_OPTIONS:
        await call.answer("Неверное значение", show_alert=True)
        return
    if game.target_score == score:
        await call.answer(f"Уже выбрано: {score}")
        return
    game.target_score = score
    ctx.store.put(game)
    await call.message.edit_text(
        _lobby_text(game), parse_mode="HTML", reply_markup=lobby_keyboard(game)
    )
    await call.answer(f"Цель матча: {score} очков")


@router.callback_query(F.data == "lobby:cancel")
async def cb_cancel(call: CallbackQuery, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(call.message.chat.id)
    if not game or game.status is not GameStatus.LOBBY:
        await call.answer("Лобби уже закрыто", show_alert=True)
        return
    if call.from_user.id != game.owner_id:
        await call.answer("Только создатель лобби.", show_alert=True)
        return
    ctx.store.delete(game.chat_id)
    await call.message.edit_text("❌ Лобби отменено.", parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "lobby:start")
async def cb_start(call: CallbackQuery, bot: Bot, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(call.message.chat.id)
    if not game or game.status is not GameStatus.LOBBY:
        await call.answer("Уже стартовано", show_alert=True)
        return
    if call.from_user.id != game.owner_id:
        await call.answer("Только создатель лобби может стартануть матч.", show_alert=True)
        return

    a_count = len(game.teams[TeamId.A].player_ids)
    b_count = len(game.teams[TeamId.B].player_ids)
    if a_count < ctx.settings.min_team_size or b_count < ctx.settings.min_team_size:
        await call.answer(
            f"Нужно минимум {ctx.settings.min_team_size} игрока в каждой команде "
            f"(сейчас {a_count} vs {b_count}).",
            show_alert=True,
        )
        return

    # Roll abilities
    assignments = assign_random_abilities(list(game.players.keys()))
    for pid, ability_id in assignments.items():
        game.players[pid].ability_id = ability_id

    # Random starting zone
    starting_zone = random.choice(non_chaos_zones())
    game.current_zone_id = starting_zone.id
    game.current_round = 1
    game.status = GameStatus.PLAYING
    game.sphere_position = (random.randint(0, 4), random.randint(0, 4))

    # Pick a random arena visual for this match
    arena_path = pick_random_arena()
    game.current_arena_image = arena_path.name if arena_path else None
    ctx.store.put(game)

    await call.message.edit_text(
        _lobby_text(game) + "\n\n🚀 Матч начинается!", parse_mode="HTML"
    )
    await call.answer()

    # Send the arena backdrop as a photo, then announce the match
    caption = _start_announcement(game)
    if arena_path and arena_path.exists():
        try:
            await bot.send_photo(
                game.chat_id,
                FSInputFile(str(arena_path)),
                caption=caption,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("failed to send arena photo, falling back to text")
            await bot.send_message(game.chat_id, caption, parse_mode="HTML")
    else:
        await bot.send_message(game.chat_id, caption, parse_mode="HTML")

    # Ability assignment for each player (private dm) — includes reserves
    for player in game.players.values():
        if player.team_id is None:
            continue
        ab = player.ability
        if ab is None:
            try:
                await bot.send_message(
                    player.user_id,
                    "🎴 <b>Способность не выпала.</b>\n"
                    "<i>Не всегда вашему персонажу может выпасть способность — "
                    "всё решает рандом.</i>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            continue
        spare_note = (
            "\n\n🪑 Ты в запасе — вступишь в игру, когда из твоей команды "
            "кто-то выбудет."
            if player.is_spare
            else ""
        )
        try:
            await bot.send_message(
                player.user_id,
                f"🎴 <b>Твоя способность в матче:</b>\n"
                f"{ab.emoji} <b>{ab.name}</b>\n"
                f"<i>{ab.description}</i>{spare_note}",
                parse_mode="HTML",
            )
        except Exception:
            await bot.send_message(
                game.chat_id,
                f"⚠️ Не могу написать {fmt_player_name(player.name)} в личку — "
                f"напиши мне /start в личке, чтобы получать карточки и скрытые подсказки. "
                f"Сейчас покажу способность в чате:\n"
                f"{ab.emoji} <b>{ab.name}</b> — <i>{ab.description}</i>",
                parse_mode="HTML",
            )

    # Hand off to gameplay
    from .gameplay import start_round

    await start_round(bot, ctx, game)


def _start_announcement(game: Game) -> str:
    arena_line = ""
    if game.current_arena_image:
        arena_line = f"🏟️ Арена: <b>{arena_display_name(game.current_arena_image)}</b>\n"
    return (
        "🚀 <b>Матч Темпал начинается!</b>\n"
        f"{arena_line}"
        f"До победы: <b>{game.target_score}</b> очков.\n"
        f"<i>Зона раунда определяется случайно перед каждым раундом.</i>\n"
        f"{format_scoreboard(game)}"
    )


# ── plain commands as alternatives to buttons ────────────────────────


@router.message(Command("join"))
async def cmd_join(message: Message, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    if not game or game.status is not GameStatus.LOBBY:
        await message.answer("Сейчас нет открытого лобби. /newgame чтобы создать.")
        return
    if message.from_user.id in game.players:
        await message.answer("Ты уже в лобби.")
        return
    game.add_player(message.from_user.id, message.from_user.full_name)
    ctx.store.put(game)
    await message.answer(f"{fmt_player_name(message.from_user.full_name)} вошёл(а) в лобби.")


@router.message(Command("leave"))
async def cmd_leave(message: Message, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    if not game or game.status is not GameStatus.LOBBY:
        return
    if game.remove_player(message.from_user.id):
        ctx.store.put(game)
        await message.answer(f"{fmt_player_name(message.from_user.full_name)} ушёл(а) из лобби.")


@router.message(Command("myteam"))
async def cmd_myteam(message: Message, **kwargs) -> None:
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    if not game or game.status is not GameStatus.LOBBY:
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or parts[1].upper() not in {"A", "Б", "B"}:
        await message.answer("Используй /myteam A или /myteam B")
        return
    team_letter = "A" if parts[1].upper() in {"A"} else "B"
    team_id = TeamId(team_letter)
    p = game.players.get(message.from_user.id) or game.add_player(
        message.from_user.id, message.from_user.full_name
    )
    if p.team_id is not None:
        game.teams[p.team_id].player_ids = [
            pid for pid in game.teams[p.team_id].player_ids if pid != p.user_id
        ]
    p.team_id = team_id
    if p.user_id not in game.teams[team_id].player_ids:
        game.teams[team_id].player_ids.append(p.user_id)
    ctx.store.put(game)
    await message.answer(f"Ты теперь в {TEAM_LABEL[team_id]}.")


@router.message(Command("startgame"))
async def cmd_startgame(message: Message, bot: Bot, **kwargs) -> None:
    """Allow /startgame as alternative to the inline button."""
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    if not game or game.status is not GameStatus.LOBBY:
        await message.answer("Лобби не открыто. /newgame чтобы открыть.")
        return
    if message.from_user.id != game.owner_id:
        await message.answer("Только создатель лобби может стартануть матч.")
        return

    # Simulate the start callback
    class FakeCall:
        def __init__(self, msg: Message):
            self.from_user = msg.from_user
            self.message = msg
            self.data = "lobby:start"

        async def answer(self, *args, **kwargs):
            return None

    await cb_start(FakeCall(message), bot, **kwargs)  # type: ignore[arg-type]


# ── /kick, /spare, /shuffle ──────────────────────────────────────────


@router.message(Command("kick"))
async def cmd_kick(message: Message, bot: Bot, **kwargs) -> None:
    """Owner / group admin can kick someone from the lobby by replying
    to that user's message with /kick."""
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    if not game or game.status is not GameStatus.LOBBY:
        await message.answer("Кикать можно только в открытом лобби.")
        return
    if not (message.reply_to_message and message.reply_to_message.from_user):
        await message.answer(
            "Ответь /kick на сообщение игрока, которого нужно выгнать из лобби."
        )
        return
    allowed = (
        message.from_user.id == game.owner_id
        or await is_group_owner_or_admin(bot, message.chat.id, message.from_user.id)
    )
    if not allowed:
        await message.answer("Только владелец группы или создатель лобби.")
        return
    target = message.reply_to_message.from_user
    if target.id == game.owner_id:
        await message.answer("Создателя лобби кикать нельзя — пусть отменит сам.")
        return
    if not game.remove_player(target.id):
        await message.answer("Этого игрока в лобби нет.")
        return
    ctx.store.put(game)
    await message.answer(
        f"🚪 {fmt_player_name(target.full_name)} выгнан(а) из лобби."
    )


@router.message(Command("spare"))
async def cmd_spare(message: Message, **kwargs) -> None:
    """Toggle the caller's spare status in the current team.

    /spare — toggle on current team; /spare A or /spare B — become spare
    on that specific team.
    """
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    if not game or game.status is not GameStatus.LOBBY:
        await message.answer("/spare работает только в открытом лобби.")
        return

    parts = (message.text or "").split()
    explicit_team: TeamId | None = None
    if len(parts) >= 2:
        letter = parts[1].upper()
        if letter not in {"A", "B", "Б"}:
            await message.answer("Используй /spare, /spare A или /spare B.")
            return
        explicit_team = TeamId.A if letter == "A" else TeamId.B

    player = game.players.get(message.from_user.id) or game.add_player(
        message.from_user.id, message.from_user.full_name
    )
    target_team = explicit_team or player.team_id
    if target_team is None:
        await message.answer(
            "Сначала выбери команду (/myteam A или /myteam B), потом /spare."
        )
        return

    # Pull out of both teams' rosters and reserve lists first.
    for tid in (TeamId.A, TeamId.B):
        game.teams[tid].player_ids = [
            pid for pid in game.teams[tid].player_ids if pid != player.user_id
        ]
        game.reserves[tid] = [
            pid for pid in game.reserves[tid] if pid != player.user_id
        ]

    if player.is_spare and explicit_team is None:
        # Toggle: was spare, become regular on same team.
        player.is_spare = False
        player.team_id = target_team
        if player.user_id not in game.teams[target_team].player_ids:
            game.teams[target_team].player_ids.append(player.user_id)
        ctx.store.put(game)
        await message.answer(
            f"{fmt_player_name(player.name)} возвращается в основной состав "
            f"{TEAM_LABEL[target_team]}."
        )
        return

    player.team_id = target_team
    player.is_spare = True
    game.reserves[target_team].append(player.user_id)
    ctx.store.put(game)
    await message.answer(
        f"🪑 {fmt_player_name(player.name)} теперь в запасе {TEAM_LABEL[target_team]}."
    )


@router.message(Command("shuffle"))
async def cmd_shuffle(message: Message, bot: Bot, **kwargs) -> None:
    """Reshuffle teams in lobby (owner / group admin only)."""
    ctx: BotContext = get_context(kwargs)
    game = ctx.store.get(message.chat.id)
    if not game or game.status is not GameStatus.LOBBY:
        await message.answer("Перетасовать можно только в открытом лобби.")
        return
    allowed = (
        message.from_user.id == game.owner_id
        or await is_group_owner_or_admin(bot, message.chat.id, message.from_user.id)
    )
    if not allowed:
        await message.answer(
            "Только владелец группы или создатель лобби может пересобрать команды."
        )
        return
    if not game.players:
        await message.answer("В лобби пока никого нет — нечего тасовать.")
        return
    _shuffle_teams(game)
    ctx.store.put(game)
    await _send_lobby(message, game)
    await message.answer("🎲 Команды перетасованы.")
