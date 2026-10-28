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


def search_letter(query: str, text: str, limit: int = 3) -> str:
    """Tool output for agents: is the query in the letter verbatim, and which lines come closest."""
    if not query.strip():
        return "Empty query."
    found = quote_in_text(query, text)
    words = set(re.findall(r"\w+", normalize(query)))
    scored = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        norm = normalize(line)
        overlap = len(words & set(re.findall(r"\w+", norm)))
        if overlap:
            scored.append((-(overlap + 10 * (normalize(query) in norm)), n, line.strip()))
    lines = [f"  line {n}: {line}" for _, n, line in sorted(scored)[:limit]]
    head = "FOUND verbatim in the letter." if found else "NOT found verbatim in the letter."
    return head + ("\nClosest lines:\n" + "\n".join(lines) if lines else "\nNo similar lines.")
