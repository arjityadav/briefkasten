"""German public holidays per Bundesland, computed (no external data).

Covers the statutory state-wide holidays. Holidays that only apply in parts of a
state (e.g. Augsburger Friedensfest, Mariä Himmelfahrt in Catholic parts of Bavaria)
are not included.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

STATES: dict[str, str] = {
    "BW": "Baden-Württemberg",
    "BY": "Bayern",
    "BE": "Berlin",
    "BB": "Brandenburg",
    "HB": "Bremen",
    "HH": "Hamburg",
    "HE": "Hessen",
    "MV": "Mecklenburg-Vorpommern",
    "NI": "Niedersachsen",
    "NW": "Nordrhein-Westfalen",
    "RP": "Rheinland-Pfalz",
    "SL": "Saarland",
    "SN": "Sachsen",
    "ST": "Sachsen-Anhalt",
    "SH": "Schleswig-Holstein",
    "TH": "Thüringen",
}


def easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _check_state(state: str) -> str:
    code = state.upper()
    if code not in STATES:
        raise ValueError(f"Unknown Bundesland '{state}'. Use one of: {', '.join(STATES)}")
    return code


@lru_cache(maxsize=256)
def public_holidays(year: int, state: str) -> dict[date, str]:
    s = _check_state(state)
    e = easter_sunday(year)
    h: dict[date, str] = {
        date(year, 1, 1): "Neujahr",
        e - timedelta(days=2): "Karfreitag",
        e + timedelta(days=1): "Ostermontag",
        date(year, 5, 1): "Tag der Arbeit",
        e + timedelta(days=39): "Christi Himmelfahrt",
        e + timedelta(days=50): "Pfingstmontag",
        date(year, 10, 3): "Tag der Deutschen Einheit",
        date(year, 12, 25): "1. Weihnachtstag",
        date(year, 12, 26): "2. Weihnachtstag",
    }
    if s in {"BW", "BY", "ST"}:
        h[date(year, 1, 6)] = "Heilige Drei Könige"
    if (s == "BE" and year >= 2019) or (s == "MV" and year >= 2023):
        h[date(year, 3, 8)] = "Internationaler Frauentag"
    if s in {"BW", "BY", "HE", "NW", "RP", "SL"}:
        h[e + timedelta(days=60)] = "Fronleichnam"
    if s == "SL":
        h[date(year, 8, 15)] = "Mariä Himmelfahrt"
    if s == "TH" and year >= 2019:
        h[date(year, 9, 20)] = "Weltkindertag"
    reformation = {"BB", "MV", "SN", "ST", "TH"} | ({"HB", "HH", "NI", "SH"} if year >= 2018 else set())
    if s in reformation or year == 2017:
        h[date(year, 10, 31)] = "Reformationstag"
    if s in {"BW", "BY", "NW", "RP", "SL"}:
        h[date(year, 11, 1)] = "Allerheiligen"
    if s == "SN":
        nov22 = date(year, 11, 22)
        h[nov22 - timedelta(days=(nov22.weekday() - 2) % 7)] = "Buß- und Bettag"
    return h


def holiday_name(d: date, state: str) -> str | None:
    return public_holidays(d.year, state).get(d)


def is_working_day(d: date, state: str) -> bool:
    return d.weekday() < 5 and holiday_name(d, state) is None


def next_working_day(d: date, state: str) -> date:
    while not is_working_day(d, state):
        d += timedelta(days=1)
    return d
