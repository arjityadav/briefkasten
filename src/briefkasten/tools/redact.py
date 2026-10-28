"""PII redaction before any text reaches an LLM.

Identifiers are replaced with stable placeholders like [IBAN_1]. The mapping stays on
local disk and is only used to put real values back into an approved reply on export.

Three layers, in order:
1. Terms the user lists in BRIEFKASTEN_REDACT_TERMS, exactly and fuzzily (OCR misreadings).
2. Automatic detection from the way German letters are written: forms of address ("Herrn Max
   Mustermann"), labels ("Ehefrau: Erika Mustermann", "geb. Müller"), the name line above a street
   in an address block, street addresses and birthdates. Every later occurrence of a detected
   name, and of its parts, is masked too. Organisations are left alone: the agents need the sender.
3. Patterns for IBANs, tax and insurance numbers, phone numbers and e-mail addresses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

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


# --------------------------------------------------------------------------- automatic detection

_UP = "A-ZÄÖÜ"
_LOW = "a-zäöüß"
NAME_WORD = rf"[{_UP}][{_LOW}]+(?:-[{_UP}][{_LOW}]+)?(?![{_UP}{_LOW}])"  # whole words only: not "Gmb" of "GmbH"
_PARTICLE = r"(?:von|van|de|zu|vom|der|den|ten)"
_NAME = rf"{NAME_WORD}(?:[ \t]+(?:{_PARTICLE}[ \t]+)*{NAME_WORD}){{0,3}}"
_TITLES = r"(?:(?:Dr|Prof|Dipl\.-Ing)\.[ \t]+)*"

HONORIFIC = re.compile(rf"\b(?:Herrn?|Frau|Hr\.|Fr\.)[ \t]+{_TITLES}({_NAME})")
LABELLED = re.compile(
    r"\b(?:Vor- und (?:Zu|Nach)name|Name|Vorname|Vornamen|Familienname|Nachname|Geburtsname|Ehegatte|Ehegattin|"
    r"Ehefrau|Ehemann|Lebenspartner(?:in)?|Partner(?:in)?|Kind(?:er)?|Tochter|Sohn|Mutter|Vater|Versicherte[rn]?|"
    r"Mitversicherte[rn]?|Antragsteller(?:in)?|Antragsgegner(?:in)?|Kontoinhaber(?:in)?|Mieter(?:in)?|Schuldner(?:in)?|"
    r"Patient(?:in)?|Kund(?:e|in)|Arbeitnehmer(?:in)?|Mitarbeiter(?:in)?|Betroffene[r]?|Beklagte[r]?|Halter(?:in)?)"
    rf"[ \t]*:[ \t]*({_NAME}(?:,[ \t]*{_NAME})?)"
)
MAIDEN = re.compile(rf"\bgeb\.[ \t]+({NAME_WORD})\b(?![ \t]*\d)")
STREET = re.compile(
    rf"\b(?:[{_UP}][\w.-]*[ \t]+){{0,2}}?[{_UP}][{_LOW}.-]*?"
    rf"(?:straße|strasse|str\.|weg|platz|allee|gasse|ring|damm|ufer|chaussee|steig|pfad)"
    rf"|\b(?:[{_UP}][\w-]*[ \t]+){{1,2}}(?:Straße|Strasse|Str\.|Weg|Platz|Allee|Gasse|Ring|Damm|Ufer)",
)
STREET_NUMBER = re.compile(rf"(?:{STREET.pattern})[ \t]+\d+[ \t]?(?:[-–][ \t]?\d+)?[a-zA-Z]?\b")
BIRTHDATE = re.compile(r"(?:\bgeb(?:oren)?\.?(?:[ \t]+am)?|\bGeburtsdatum[ \t]*:?)[ \t]+(\d{1,2}\.\d{1,2}\.\d{2,4})")

# Words that end a name or show it is not a person.
STOPWORDS = {
    "Ihnen", "Ihr", "Ihre", "Ihren", "Ihrem", "Ihrer", "Sie", "Wir", "Uns", "Mit", "Bitte", "Der", "Die", "Das", "Den",
    "Dem", "Und", "Im", "Auftrag", "Freundlichen", "Grüßen", "Damen", "Herren", "Sehr", "Geehrte", "Geehrter",
    "Herr", "Herrn", "Frau", "Hiermit", "Wenn", "Für", "Bei", "Zu", "Am", "Vom", "Nach", "Ab", "Bis",
}
ORG_HINTS = re.compile(
    r"(?:GmbH|AG|KG|e\.\s?V\.|mbH|SE|OHG|UG|Amt|amt|Kasse|kasse|Gericht|gericht|Bank|bank|Behörde|Stadt|Gemeinde|"
    r"Landkreis|Jobcenter|Agentur|Versicherung|Inkasso|Hausverwaltung|Verwaltung|Service|Zentrum|Stelle|stelle|"
    r"AOK|BKK|IKK|DAK|Barmer|Techniker|Knappschaft|Universität|Hochschule|Schule|Klinik|Klinikum|Praxis|Rundfunk)"
)


def _clean_name(raw: str) -> str | None:
    """Trim a candidate at the first stopword; None if it is empty, an organisation or a single short word."""
    words = []
    for w in re.split(r"[ \t]+", raw.strip().rstrip(",")):
        if w.rstrip(",") in STOPWORDS:
            break
        words.append(w)
    name = " ".join(words).strip(" ,")
    if not name or ORG_HINTS.search(name) or len(name) < 3 or STREET.fullmatch(name):
        return None
    return name


def _line_at(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start : end if end != -1 else len(text)]


def detect_people(text: str) -> tuple[list[str], list[str], list[str]]:
    """(names, street addresses, birthdates) found by letter conventions, longest first."""
    names: set[str] = set()
    for rx in (HONORIFIC, LABELLED, MAIDEN):
        for m in rx.finditer(text):
            if ORG_HINTS.search(_line_at(text, m.start())):  # "Antragsteller: Fitness Plus GmbH"
                continue
            for part in m.group(1).split(","):  # "Mustermann, Erika"
                if (n := _clean_name(part)) is not None:
                    names.add(n)
    lines = text.splitlines()
    for above, line in zip(lines, lines[1:]):
        # Address block: a name line right above a street line; payslips put other columns after it.
        m = re.fullmatch(rf"[ \t]*{_TITLES}({_NAME})(?:[ \t]{{2,}}.*)?", above)
        if m and STREET_NUMBER.search(line) and (n := _clean_name(m.group(1))) is not None and len(n.split()) >= 2:
            names.add(n)
    streets = set()
    for i, line in enumerate(lines):
        # A street next to an organisation's name is its public address, which the agents need ("send the
        # objection to ..."); every other street is treated as personal.
        if ORG_HINTS.search(line) or (i and ORG_HINTS.search(lines[i - 1])):
            continue
        streets |= {m.group(0).strip() for m in STREET_NUMBER.finditer(line)}
    births = {m.group(1) for m in BIRTHDATE.finditer(text)}
    order = lambda xs: sorted(xs, key=len, reverse=True)
    return order(names), order(streets), order(births)


class Redactor:
    def __init__(self, terms: list[str] | None = None, auto: bool = True):
        # Longest first, so "Max Mustermann" wins over "Mustermann".
        self.terms = sorted({t for t in (terms or []) if len(t) >= 3}, key=len, reverse=True)
        self.auto = auto  # detect unlisted names, street addresses and birthdates

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
        # Second pass for text from OCR or typed with variants: "Musterstrake 12" (misread ß), "Musterstr. 12".
        # Matches map to the term as the user wrote it, so an export restores the correct spelling.
        for term in self.terms:
            text = _fuzzy_sub(term, text, lambda t=term: placeholder("PERSONAL", t))

        if self.auto:
            names, streets, births = detect_people(text)
            for b in births:
                text = re.sub(rf"(?<!\d){re.escape(b)}(?!\d)", lambda m: placeholder("BIRTHDATE", m.group(0)), text)
            for st in streets:
                text = re.sub(rf"(?<!\w){re.escape(st)}(?!\w)", lambda m: placeholder("ADDRESS", m.group(0)), text)
            # Whole names first, then each part on its own ("Erika" later in the letter), longest first.
            parts = {w for n in names for w in n.split() if len(w) >= 3 and w not in STOPWORDS and not re.fullmatch(_PARTICLE, w)}
            for name in [*names, *sorted(parts - set(names), key=len, reverse=True)]:
                pattern = re.compile(r"(?<![\w\[])" + r"[ \t]+".join(map(re.escape, name.split())) + r"(?![\w\]])")
                text = pattern.sub(lambda m: placeholder("NAME", m.group(0)), text)
                if " " in name:
                    text = _fuzzy_sub(name, text, lambda n=name: placeholder("NAME", n))
        for label, pattern in PATTERNS:
            text = pattern.sub(lambda m, lb=label: placeholder(lb, m.group(0)), text)
        return Redaction(text=text, mapping=mapping)


FUZZY_MIN_RATIO = 0.85  # similarity needed to mask a near-match; masking too much is the safe direction
FUZZY_MIN_LENGTH = 6  # shorter terms are only matched exactly
_TOKEN = re.compile(r"[^\s,;:()\[\]]+")
_FOLD = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})


def _fold(s: str) -> str:
    """Spelling-insensitive form: case, umlauts, ß/ss, 'straße'/'str.', punctuation."""
    s = s.casefold().replace("ae", "a").replace("oe", "o").replace("ue", "u").translate(_FOLD)
    s = re.sub(r"str(asse|\.)", "str", s)
    return re.sub(r"[^\w ]", "", s)


def _fuzzy_sub(term: str, text: str, make) -> str:
    target = _fold(term)
    n = len(term.split())
    if len(target) < FUZZY_MIN_LENGTH:
        return text
    tokens = list(_TOKEN.finditer(text))
    spans = []
    i = 0
    while i + n <= len(tokens):
        window = tokens[i : i + n]
        if not any(PLACEHOLDER.fullmatch(t.group(0)) for t in window):
            candidate = _fold(" ".join(t.group(0) for t in window))
            if candidate and SequenceMatcher(None, candidate, target).ratio() >= FUZZY_MIN_RATIO:
                spans.append((window[0].start(), window[-1].end()))
                i += n
                continue
        i += 1
    for start, end in reversed(spans):
        text = text[:start] + make() + text[end:]
    return text


def restore(text: str, mapping: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)
