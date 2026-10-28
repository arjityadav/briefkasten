"""Intake: turn uploaded files into plain text.

Supported: text (.txt .md), Word (.docx), PDFs with a text layer, scanned PDFs and photos
(.png .jpg .jpeg .webp .heic .heif, transcribed by the vision model), and e-mails (.eml, body plus
supported attachments). Several files form one document in the given order, e.g. one photo per page.
"""

from __future__ import annotations

import email
import html
import io
import re
import zipfile
from email import policy
from pathlib import Path
from xml.etree import ElementTree

from .agents import TranscriberAgent
from .llm import Image, LLMRouter
from .models import TraceEvent

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
HEIC_TYPES = {".heic", ".heif"}  # iPhone photos; converted to JPEG (needs the 'photos' extra)
TEXT_TYPES = {".txt", ".md"}
SUPPORTED = TEXT_TYPES | {".pdf", ".docx", ".eml"} | set(IMAGE_TYPES) | HEIC_TYPES
MIN_PDF_TEXT = 80  # fewer characters than this means "probably a scan"
MAX_PDF_PAGES = 4  # scanned pages sent to the vision model per PDF
IMAGES_PER_CALL = 4  # pages per transcription call
MAX_IMAGE_SIDE = 2048  # photos are downscaled to this before upload (when Pillow is installed)
MAX_RAW_IMAGE = 4 * 1024 * 1024  # without Pillow, larger images are refused rather than rejected by the API
PAGE_BREAK = "\n\n"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class UnsupportedDocument(ValueError):
    pass


# --------------------------------------------------------------------------- formats


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _pdf_images(data: bytes) -> list[Image]:
    try:
        import pypdfium2 as pdfium
    except ImportError as e:  # pragma: no cover - optional dependency
        raise UnsupportedDocument(
            "This PDF looks scanned (no text layer). Install the 'scans' extra (pypdfium2) or upload a photo instead."
        ) from e
    pdf = pdfium.PdfDocument(data)
    images = []
    for i in range(min(len(pdf), MAX_PDF_PAGES)):
        buf = io.BytesIO()
        pdf[i].render(scale=2).to_pil().save(buf, format="PNG")
        images.append(Image("image/png", buf.getvalue()))
    return images


def _docx_text(data: bytes) -> str:
    """Paragraphs and table rows of a Word document, in reading order (stdlib only)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            root = ElementTree.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        raise UnsupportedDocument("This .docx file could not be read.") from e

    def para(p) -> str:
        out = []
        for node in p.iter():
            if node.tag == W + "t" and node.text:
                out.append(node.text)
            elif node.tag == W + "tab":
                out.append("\t")
            elif node.tag in (W + "br", W + "cr"):
                out.append("\n")
        return "".join(out)

    lines = []
    body = root.find(W + "body")
    for block in body if body is not None else []:
        if block.tag == W + "p":
            lines.append(para(block))
        elif block.tag == W + "tbl":
            for row in block.iter(W + "tr"):
                cells = [" ".join(para(p) for p in cell.iter(W + "p")).strip() for cell in row.iter(W + "tc")]
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", markup)
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


def _image(ext: str, data: bytes) -> Image:
    """Normalise a photo: apply EXIF rotation, convert HEIC, downscale large images."""
    try:
        from PIL import Image as PILImage
        from PIL import ImageOps
    except ImportError:
        if ext in HEIC_TYPES:
            raise UnsupportedDocument("iPhone photos (.heic) need the 'photos' extra: pip install 'briefkasten[photos]'") from None
        if len(data) > MAX_RAW_IMAGE:
            raise UnsupportedDocument(
                "This photo is larger than 4 MB. Install the 'photos' extra to shrink it automatically, or upload a smaller one."
            ) from None
        return Image(IMAGE_TYPES[ext], data)
    if ext in HEIC_TYPES:
        try:
            from pillow_heif import register_heif_opener
        except ImportError:
            raise UnsupportedDocument("iPhone photos (.heic) need the 'photos' extra: pip install 'briefkasten[photos]'") from None
        register_heif_opener()
    try:
        img = ImageOps.exif_transpose(PILImage.open(io.BytesIO(data)))
    except Exception as e:  # Pillow raises many types for broken files
        raise UnsupportedDocument("This image could not be read.") from e
    if ext not in HEIC_TYPES and max(img.size) <= MAX_IMAGE_SIDE and len(data) <= MAX_RAW_IMAGE:
        return Image(IMAGE_TYPES[ext], data)  # already fine: send the original bytes
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    return Image("image/jpeg", buf.getvalue())


# --------------------------------------------------------------------------- one file -> parts


def _parts(data: bytes, filename: str, depth: int = 0) -> list[str | list[Image]]:
    """A file as an ordered list of text blocks and image groups (images still need transcribing)."""
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise UnsupportedDocument(f"Unsupported file type '{ext or filename}'. Supported: {', '.join(sorted(SUPPORTED))}")
    if ext in TEXT_TYPES:
        return [_decode(data)]
    if ext == ".docx":
        return [_docx_text(data)]
    if ext == ".pdf":
        text = _pdf_text(data)
        return [text] if len(text.strip()) >= MIN_PDF_TEXT else [_pdf_images(data)]
    if ext == ".eml":
        return _email_parts(data, depth)
    return [[_image(ext, data)]]


def _email_parts(data: bytes, depth: int) -> list[str | list[Image]]:
    msg = email.message_from_bytes(data, policy=policy.default)
    head = "\n".join(f"{label}: {msg[key]}" for key, label in (("From", "Von"), ("Date", "Datum"), ("Subject", "Betreff")) if msg[key])
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        content = body_part.get_content()
        body = _html_to_text(content) if body_part.get_content_type() == "text/html" else content
    parts: list[str | list[Image]] = [f"E-Mail\n{head}\n\n{body.strip()}"]
    if depth > 0:  # attachments of attached e-mails are not unpacked further
        return parts
    for att in msg.iter_attachments():
        name = att.get_filename() or ""
        if Path(name).suffix.lower() not in SUPPORTED:
            parts.append(f"[Anhang nicht gelesen: {name or att.get_content_type()}]")
            continue
        parts.append(f"--- Anhang: {name} ---")
        parts += _parts(att.get_payload(decode=True) or b"", name, depth + 1)
    return parts


# --------------------------------------------------------------------------- public API


def load_documents(files: list[tuple[bytes, str]], router: LLMRouter | None, trace: list[TraceEvent]) -> str:
    """Several files that together form one document (e.g. one photo per page), in order."""
    if not files:
        raise UnsupportedDocument("No file given")
    parts: list[str | list[Image]] = []
    for data, name in files:
        for p in _parts(data, name):
            if isinstance(p, list) and parts and isinstance(parts[-1], list):
                parts[-1] = parts[-1] + p  # consecutive pages are transcribed together
            else:
                parts.append(p)

    texts = []
    for p in parts:
        if isinstance(p, str):
            texts.append(p)
            continue
        if router is None:
            raise UnsupportedDocument("Reading images needs a vision-capable model provider.")
        agent = TranscriberAgent(router)
        texts += [agent.run(p[i : i + IMAGES_PER_CALL], trace) for i in range(0, len(p), IMAGES_PER_CALL)]
    return PAGE_BREAK.join(t.strip() for t in texts if t.strip())


def load_document(data: bytes, filename: str, router: LLMRouter | None, trace: list[TraceEvent]) -> str:
    return load_documents([(data, filename)], router, trace)
