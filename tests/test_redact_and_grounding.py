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


def test_ocr_misreadings_and_spelling_variants_are_still_masked():
    from briefkasten.tools.redact import Redactor, restore

    r = Redactor(["Max Mustermann", "Musterstraße 12"]).redact(
        "Herrn\nMax Musterrnann\nMusterstrake 12\n70173 Stuttgart\nauch: Musterstr. 12 und MUSTERSTRASSE 12"
    )
    for leaked in ("Musterrnann", "Musterstrake", "Musterstr. 12", "MUSTERSTRASSE"):
        assert leaked not in r.text, r.text
    assert "70173 Stuttgart" in r.text  # unrelated text stays readable
    # Fuzzy matches restore to the spelling the user configured, not the OCR's
    assert sorted(set(restore(r.text, r.mapping).split("\n")[1:3])) == ["Max Mustermann", "Musterstraße 12"]


def test_fuzzy_matching_leaves_similar_but_different_words_alone():
    from briefkasten.tools.redact import Redactor

    text = "Finanzamt Musterstadt, Postfach 10 20 30"
    assert Redactor(["Mustermann"]).redact(text).text == text


# --------------------------------------------------------------------------- automatic detection of unlisted people

FAMILY_LETTER = """AOK Baden-Württemberg
Presselstraße 19, 70191 Stuttgart

Frau
Erika Musterfrau
Lindenweg 7a
70499 Stuttgart

Fragebogen zur Familienversicherung

Sehr geehrte Frau Musterfrau,

für die Familienversicherung benötigen wir Angaben zu Ihren Angehörigen:
Ehemann: Jonas Musterfrau, geb. am 14.02.1988
Kind: Lina Musterfrau, geboren 03.09.2019

Bitte senden Sie den Fragebogen bis zum 20.11.2026 zurück. Lina ist bis zum 18. Lebensjahr beitragsfrei versichert.
Ihre Ansprechpartnerin, Frau Dr. Yilmaz, erreichen Sie werktags.

Mit freundlichen Grüßen
Ihre AOK"""


def test_unlisted_family_members_addresses_and_birthdates_are_masked():
    from briefkasten.tools.redact import Redactor, restore

    r = Redactor([]).redact(FAMILY_LETTER)  # nothing listed by the user
    for secret in ("Erika", "Musterfrau", "Jonas", "Lina", "Lindenweg 7a", "14.02.1988", "03.09.2019", "Yilmaz"):
        assert secret not in r.text, (secret, r.text)
    # What the agents need stays: the sender, its public address, the deadline and the salutation
    for kept in ("AOK Baden-Württemberg", "Presselstraße 19", "20.11.2026", "Sehr geehrte", "70499 Stuttgart"):
        assert kept in r.text, kept
    labels = {ph.strip("[]").rsplit("_", 1)[0] for ph in r.mapping}
    assert labels == {"NAME", "ADDRESS", "BIRTHDATE"}
    assert restore(r.text, r.mapping) == FAMILY_LETTER


def test_detection_leaves_organisations_and_ordinary_text_alone():
    from briefkasten.tools.redact import Redactor

    text = (
        "Antragsteller: Fitness Plus GmbH, Industriestraße 4, 70565 Stuttgart\n"
        "Sehr geehrte Damen und Herren,\nbitte zahlen Sie bis zum 16.11.2026. Der Betrag beträgt 412,00 €."
    )
    assert Redactor([]).redact(text).text == text


def test_auto_detection_can_be_switched_off():
    from briefkasten.tools.redact import Redactor

    assert "Jonas" in Redactor([], auto=False).redact(FAMILY_LETTER).text


def test_models_never_see_unlisted_family_names(tmp_path):
    """End to end with no redact terms configured: the agents only ever see placeholders."""
    from datetime import date

    from briefkasten.config import Settings
    from briefkasten.demo import load_replay
    from briefkasten.orchestrator import Briefkasten

    _, _, router = load_replay("finanzamt_bescheid")
    Briefkasten(router, Settings(data_dir=tmp_path)).process(FAMILY_LETTER, today=date(2026, 10, 28))
    prompts = " ".join(c["system"] + c["prompt"] for c in router.smart.calls)
    assert prompts and not any(s in prompts for s in ("Jonas", "Lina", "Erika", "Musterfrau", "Lindenweg", "14.02.1988"))
