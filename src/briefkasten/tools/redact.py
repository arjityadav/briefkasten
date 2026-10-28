"""PII redaction before any text reaches an LLM.

Identifiers are replaced with stable placeholders like [IBAN_1]. The mapping stays on
local disk and is only used to put real values back into an approved reply on export.
Names and addresses are not reliably detectable with patterns, so the user lists them
in BRIEFKASTEN_REDACT_TERMS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Order matters: IBANs contain digit runs the later patterns would also match.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("STEUERNUMMER", re.compile(r"(?<![\d/])\d{2,3}/\d{3,4}/\d{4,5}(?![\d/])")),
    ("STEUER_ID", re.compile(r"(?<![\d+])\d{2} ?\d{3} ?\d{3} ?\d{3}(?!\d)")),
    ("RV_NUMMER", re.compile(r"\b\d{2} ?\d{6} ?[A-Z] ?\d{3}\b")),
    ("KV_NUMMER", re.compile(r"\b[A-Z]\d{9}\b")),
    ("PHONE", re.compile(r"(?<![\w/.,])(?:\+49|0)[\d ()/-]{7,}\d(?!\d|[,.]\d)")),
]
PLACEHOLDER = re.compile(r"\[[A-Z_]+_\d+\]")
EDITORIAL_PLACEHOLDER = re.compile(r"\[[^\]\n]{2,80}\]")


def open_placeholders(text: str, redactions: dict[str, str]) -> list[str]:
    """Editorial blanks like [Begründung ergänzen] that a human still has to fill in."""
    return [m for m in EDITORIAL_PLACEHOLDER.findall(text) if not (PLACEHOLDER.fullmatch(m) and m in redactions)]


@dataclass
class Redaction:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)  # placeholder -> original

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for ph in self.mapping:
            label = ph.strip("[]").rsplit("_", 1)[0]
            out[label] = out.get(label, 0) + 1
        return out


class Redactor:
    def __init__(self, terms: list[str] | None = None):
        # Longest first, so "Max Mustermann" wins over "Mustermann".
        self.terms = sorted({t for t in (terms or []) if len(t) >= 3}, key=len, reverse=True)

    def redact(self, text: str) -> Redaction:
        mapping: dict[str, str] = {}
        reverse: dict[tuple[str, str], str] = {}
        counters: dict[str, int] = {}

        def placeholder(label: str, original: str) -> str:
            key = (label, re.sub(r"\s+", "", original).lower())
            if key not in reverse:
                counters[label] = counters.get(label, 0) + 1
                ph = f"[{label}_{counters[label]}]"
                reverse[key] = ph
                mapping[ph] = original
            return reverse[key]

        for term in self.terms:
            pattern = re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, term.split())) + r"(?!\w)", re.I)
            text = pattern.sub(lambda m: placeholder("PERSONAL", m.group(0)), text)
        for label, pattern in PATTERNS:
            text = pattern.sub(lambda m, lb=label: placeholder(lb, m.group(0)), text)
        return Redaction(text=text, mapping=mapping)


def restore(text: str, mapping: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)
