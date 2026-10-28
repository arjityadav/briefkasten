"""Intake: turn an uploaded file into plain text."""

from __future__ import annotations

import io
from pathlib import Path

from .agents import TranscriberAgent
from .llm import Image, LLMRouter
from .models import TraceEvent

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
TEXT_TYPES = {".txt", ".md"}
SUPPORTED = TEXT_TYPES | {".pdf"} | set(IMAGE_TYPES)
MIN_PDF_TEXT = 80  # fewer characters than this means "probably a scan"


class UnsupportedDocument(ValueError):
    pass


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


def _pdf_images(data: bytes, max_pages: int = 4) -> list[Image]:
    try:
        import pypdfium2 as pdfium
    except ImportError as e:  # pragma: no cover - optional dependency
        raise UnsupportedDocument(
            "This PDF looks scanned (no text layer). Install the 'scans' extra (pypdfium2) or upload a photo instead."
        ) from e
    pdf = pdfium.PdfDocument(data)
    images = []
    for i in range(min(len(pdf), max_pages)):
        buf = io.BytesIO()
        pdf[i].render(scale=2).to_pil().save(buf, format="PNG")
        images.append(Image("image/png", buf.getvalue()))
    return images


def load_document(data: bytes, filename: str, router: LLMRouter | None, trace: list[TraceEvent]) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise UnsupportedDocument(f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(SUPPORTED))}")
    if ext in TEXT_TYPES:
        return _decode(data)
    if ext == ".pdf":
        text = _pdf_text(data)
        if len(text.strip()) >= MIN_PDF_TEXT:
            return text
        images = _pdf_images(data)
    else:
        images = [Image(IMAGE_TYPES[ext], data)]
    if router is None:
        raise UnsupportedDocument("Reading images needs a vision-capable model provider.")
    return TranscriberAgent(router).run(images, trace)
