"""Checks that extracted facts are actually in the letter (anti-hallucination)."""

from __future__ import annotations

import re
import unicodedata
from datetime import date

MONTHS_DE = [
    "", "januar", "februar", "märz", "april", "mai", "juni",
    "juli", "august", "september", "oktober", "november", "dezember",
]
_TRANSLATE = str.maketrans({"„": '"', "“": '"', "”": '"', "‚": "'", "‘": "'", "’": "'", "–": "-", "—": "-", " ": " "})


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).translate(_TRANSLATE)
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_in_text(quote: str, text: str) -> bool:
    """True if the quote appears in the text, allowing '...' gaps and whitespace differences."""
    hay = normalize(text)
    parts = [p.strip(" .\"'") for p in re.split(r"\.\.\.|…", normalize(quote))]
    parts = [p for p in parts if p]
    if not parts or sum(len(p) for p in parts) < 4:
        return False
    pos = 0
    for part in parts:
        idx = hay.find(part, pos)
        if idx == -1:
            return False
        pos = idx + len(part)
    return True


def _amount_variants(value: float) -> set[str]:
    german = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    variants = {german, german.replace(".", "")}
    if float(value).is_integer():
        n = int(value)
        variants |= {str(n), f"{n:,}".replace(",", "."), f"{german[:-3]},-"}
    return variants


def amount_in_text(value: float, text: str) -> bool:
    hay = normalize(text)
    for v in _amount_variants(value):
        if re.search(r"(?<![\d.,])" + re.escape(v) + r"(?![\d]|,\d)", hay):
            return True
    return False


def date_in_text(d: date, text: str) -> bool:
    hay = normalize(text)
    variants = {
        f"{d.day:02d}.{d.month:02d}.{d.year}",
        f"{d.day}.{d.month}.{d.year}",
        f"{d.day:02d}.{d.month:02d}.{d.year % 100:02d}",
        f"{d.day}. {MONTHS_DE[d.month]} {d.year}",
        f"{d.day:02d}. {MONTHS_DE[d.month]} {d.year}",
        d.isoformat(),
    }
    return any(re.search(r"(?<!\d)" + re.escape(v) + r"(?!\d)", hay) for v in variants)
