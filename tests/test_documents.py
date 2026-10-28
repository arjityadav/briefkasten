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
    import json

    golds = list((SAMPLES / "gold").glob("*.json"))
    assert len(golds) >= 8
    for g in golds:  # every hand-labelled answer points at a sample that exists
        assert (SAMPLES / "letters" / json.loads(g.read_text(encoding="utf-8"))["letter"]).is_file(), g.name


# --------------------------------------------------------------------------- more formats

from briefkasten.documents import load_documents  # noqa: E402
from briefkasten.llm import LLMRouter, ScriptedLLM  # noqa: E402


def _docx(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    import io
    import zipfile
    from xml.sax.saxutils import escape

    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    if table:
        rows = "".join(
            "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{escape(c)}</w:t></w:r></w:p></w:tc>" for c in r) + "</w:tr>" for r in table
        )
        body += f"<w:tbl>{rows}</w:tbl>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {w}><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def test_docx_paragraphs_and_tables():
    data = _docx(["Sehr geehrte Damen und Herren,", "bitte zahlen Sie bis zum 16.11.2026."], [["Betrag", "412,00 €"]])
    text = load_document(data, "brief.docx", None, [])
    assert text.splitlines() == ["Sehr geehrte Damen und Herren,", "bitte zahlen Sie bis zum 16.11.2026.", "Betrag | 412,00 €"]


def test_email_body_and_attachments():
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = "Hausverwaltung <info@example.org>"
    msg["Subject"] = "Nebenkostenabrechnung 2025"
    msg.set_content("Anbei die Abrechnung.")
    msg.add_alternative("<p>Anbei die <b>Abrechnung</b>.</p>", subtype="html")
    msg.add_attachment("Nachzahlung: 238,47 €".encode(), maintype="text", subtype="plain", filename="abrechnung.txt")
    msg.add_attachment(_docx(["Zweite Seite"]), maintype="application", subtype="octet-stream", filename="seite2.docx")
    msg.add_attachment(b"PK...", maintype="application", subtype="zip", filename="archiv.zip")

    text = load_document(msg.as_bytes(), "mail.eml", None, [])
    assert "Betreff: Nebenkostenabrechnung 2025" in text and "Anbei die Abrechnung." in text
    assert "--- Anhang: abrechnung.txt ---\n\nNachzahlung: 238,47 €" in text
    assert "Zweite Seite" in text and "[Anhang nicht gelesen: archiv.zip]" in text


def _png(seed: int) -> bytes:
    try:
        import io

        from PIL import Image as PILImage
    except ImportError:
        return b"\x89PNG" + bytes([seed])  # without Pillow the bytes are passed through unopened
    buf = io.BytesIO()
    PILImage.new("RGB", (10, 10), (seed, seed, seed)).save(buf, format="PNG")
    return buf.getvalue()


def test_photo_pages_are_transcribed_together_in_order():
    llm = ScriptedLLM({"transcriber": ["Seite 1-4", "Seite 5"]})
    trace = []
    pages = [(_png(i), f"seite{i}.png") for i in range(1, 6)]
    text = load_documents([(b"Begleitschreiben", "notiz.txt"), *pages], LLMRouter(llm, llm), trace)
    assert text == "Begleitschreiben\n\nSeite 1-4\n\nSeite 5"
    assert [e.agent for e in trace] == ["transcriber", "transcriber"]  # 4 pages per call
    assert trace[0].model == "scripted-replay"


def test_photo_needs_extras_when_missing(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_pil(name, *a, **k):
        if name.startswith("PIL") or name.startswith("pillow_heif"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_pil)
    with pytest.raises(UnsupportedDocument, match="photos"):
        load_document(b"heic", "IMG_0001.HEIC", None, [])
    with pytest.raises(UnsupportedDocument, match="larger than 4 MB"):
        load_document(b"\xff" * (4 * 1024 * 1024 + 1), "foto.jpg", None, [])


def test_large_photo_is_downscaled_when_pillow_is_available():
    PIL = pytest.importorskip("PIL.Image")
    import io

    from briefkasten.documents import _image

    buf = io.BytesIO()
    PIL.new("RGB", (4000, 3000), "white").save(buf, format="PNG")
    img = _image(".png", buf.getvalue())
    assert img.media_type == "image/jpeg"
    assert max(PIL.open(io.BytesIO(img.data)).size) == 2048
