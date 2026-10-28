from datetime import date

from briefkasten.llm import extract_json
from briefkasten.tools.grounding import amount_in_text, date_in_text, quote_in_text
from briefkasten.tools.redact import Redactor, restore

LETTER = """Herrn Max Mustermann, Musterstraße 12
Steuernummer: 99/815/08154   Identifikationsnummer: 12 345 678 901
IBAN: DE89 3704 0044 0532 0130 00  Versichertennummer: A123456789
Rentenversicherungsnummer: 12 150780 J 123
Telefon 0711 1234567, E-Mail max@example.de
Datum: 13.10.2026  Betrag: 1.680,00 €  Frist bis 5. November 2026"""


def test_redacts_identifiers_and_terms():
    r = Redactor(["Max Mustermann", "Musterstraße 12"]).redact(LETTER)
    for secret in ["Max Mustermann", "99/815/08154", "12 345 678 901", "DE89 3704", "A123456789", "12 150780 J 123", "0711 1234567", "max@example.de"]:
        assert secret not in r.text, secret
    assert r.counts == {"PERSONAL": 2, "IBAN": 1, "EMAIL": 1, "STEUERNUMMER": 1, "STEUER_ID": 1, "RV_NUMMER": 1, "KV_NUMMER": 1, "PHONE": 1}
    # dates and amounts must survive, the agents need them
    assert "13.10.2026" in r.text and "1.680,00" in r.text


def test_restore_round_trip_and_stable_placeholders():
    text = "Steuernummer 99/815/08154 und nochmal 99/815/08154"
    r = Redactor().redact(text)
    assert r.text == "Steuernummer [STEUERNUMMER_1] und nochmal [STEUERNUMMER_1]"
    assert restore(r.text, r.mapping) == text
    assert restore("[UNKNOWN_1] stays", r.mapping) == "[UNKNOWN_1] stays"


def test_longest_term_wins():
    r = Redactor(["Mustermann", "Max Mustermann"]).redact("Max Mustermann / Herr Mustermann")
    assert r.text == "[PERSONAL_1] / Herr [PERSONAL_2]"


def test_quote_matching_is_whitespace_and_typography_tolerant():
    text = "verbleibende Beträge          412,00 €\nDie Frist beträgt „einen Monat“."
    assert quote_in_text("verbleibende Beträge 412,00 €", text)
    assert quote_in_text('Die Frist beträgt "einen Monat"', text)
    assert quote_in_text("verbleibende ... 412,00", text)
    assert not quote_in_text("Die Frist beträgt zwei Monate", text)
    assert not quote_in_text("..", text)


def test_amount_and_date_grounding():
    assert amount_in_text(1680.0, LETTER)
    assert not amount_in_text(168.0, LETTER)
    assert not amount_in_text(1860.0, LETTER)
    assert date_in_text(date(2026, 10, 13), LETTER)
    assert date_in_text(date(2026, 11, 5), LETTER)
    assert not date_in_text(date(2026, 10, 3), LETTER)


def test_extract_json_handles_fences_and_prose():
    assert extract_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here: {"a": {"b": 2}} done') == {"a": {"b": 2}}


def test_phone_edge_cases():
    red = lambda t: Redactor().redact(t).text  # noqa: E731
    assert red("Tel. +49 711 123456.") == "Tel. [PHONE_1]."
    assert red("Betrag 0,00 € am 01.01.2027") == "Betrag 0,00 € am 01.01.2027"
    assert red("Summe 0711,50 €") == "Summe 0711,50 €"
