from datetime import date

import pytest

from briefkasten.config import Settings
from briefkasten.demo import DEMO_REDACT_TERMS, SAMPLES, load_replay
from briefkasten.documents import UnsupportedDocument, load_document
from briefkasten.orchestrator import Briefkasten


def _pdf_from_text(text: str) -> bytes:
    pytest.importorskip("reportlab")
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in text.splitlines():
        c.drawString(40, y, line)
        y -= 14
        if y < 40:
            c.showPage()
            y = 800
    c.save()
    return buf.getvalue()


def test_text_pdf_goes_through_the_pipeline(tmp_path):
    letter, intent, router = load_replay("finanzamt_bescheid")
    pdf = _pdf_from_text(letter)
    bk = Briefkasten(router, Settings(data_dir=tmp_path, redact_terms=DEMO_REDACT_TERMS))
    state = bk.process_file(pdf, "bescheid.pdf", reply_intent=intent, today=date(2026, 10, 28))
    assert state.status == "awaiting_approval", state.error
    assert state.verification.passed
    assert "[STEUERNUMMER_1]" in state.text_redacted


def test_cp1252_text_is_decoded():
    text = load_document("Straße".encode("cp1252"), "brief.txt", None, [])
    assert text == "Straße"


def test_unsupported_and_image_without_model():
    with pytest.raises(UnsupportedDocument):
        load_document(b"x", "brief.docx", None, [])
    with pytest.raises(UnsupportedDocument):
        load_document(b"\x89PNG", "foto.png", None, [])


def test_samples_are_packaged():
    assert len(list((SAMPLES / "letters").glob("*.txt"))) == 4
    assert len(list((SAMPLES / "gold").glob("*.json"))) == 4
