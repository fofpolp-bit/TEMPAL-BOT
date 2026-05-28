"""Memey, in-spirit-with-the-meta achievements awarded during a Темпал match."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Achievement:
    id: str
    name: str
    emoji: str
    description: str


ACHIEVEMENTS: dict[str, Achievement] = {
    a.id: a
    for a in [
        Achievement(
            id="married_to_sphere",
            name="Замуж за сферу",
            emoji="💍",
            description="Захватил(а) сферу три раунда подряд.",
        ),
        Achievement(
            id="shavermoverum",
            name="Шавермоверум",
            emoji="🥶",
            description="Успешно заморозил(а) противников три раза за матч.",
        ),
        Achievement(
            id="vetorio_v_shoke",
            name="Веторио в шоке",
            emoji="🤯",
            description="Выкинул(а) натуральную 20 в роллах три раза.",
        ),
        Achievement(
            id="kubik_protiv_menya",
            name="Кубик против меня",
            emoji="🎲",
            description="Выкинул(а) натуральную 1 три раза за матч.",
        ),
        Achievement(
            id="menya_tut_ne_bylo",
            name="Меня тут не было",
            emoji="🧊",
            description="Был(а) полностью заморожен(а) два раунда за матч.",
        ),
        Achievement(
            id="tachion_v_kofeyne",
            name="Тахион в кофейне",
            emoji="☕",
            description="Три полных успеха подряд.",
        ),
        Achievement(
            id="ya_ne_pasuyu",
            name="Я не пасую, я экономлю",
            emoji="💤",
            description="Пасанул(а) три и более раундов за матч.",
        ),
        Achievement(
            id="kalkulyator",
            name="Калькулятор",
            emoji="🧮",
            description="Выкинул(а) натуральную 10 ровно три раза.",
        ),
        Achievement(
            id="temporal_expert",
            name="Темпорал-эксперт",
            emoji="🧙",
            description="Успешно применил(а) способность четыре раза за матч.",
        ),
        Achievement(
            id="zerohero",
            name="Зерохиро",
            emoji="🦸",
            description="Команда вышла с 0 очков на победу после отставания.",
        ),
    ]
}


def get_achievement(achievement_id: str) -> Achievement:
    return ACHIEVEMENTS[achievement_id]
