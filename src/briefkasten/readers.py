"""Image readers: how scans and photos become text, before redaction is possible.

Redaction works on text, so whatever reads an image sees the unredacted letter. The default
therefore reads images on this machine:

    ocr           Tesseract OCR, locally. The image never leaves the machine. (default)
    local_vision  A vision model on a local OpenAI-compatible server (e.g. Ollama). Refused if
                  the server address is not local.
    cloud         The provider's vision model (e.g. Qwen on Groq). The unredacted image is sent
                  to that provider. Best quality on poor photos; an explicit opt-in.
"""

from __future__ import annotations

import ipaddress
import shutil
import subprocess
import time
from typing import Callable
from urllib.parse import urlparse

from .agents import TranscriberAgent
from .agents.base import _report
from .config import Settings
from .llm import Image, LLMError, LLMRouter
from .models import TraceEvent

ImageReader = Callable[[list[Image], list[TraceEvent]], str]

READERS = {
    "ocr": "local OCR (Tesseract); the image never left this machine",
    "local_vision": "a local vision model; the image never left this machine",
    "cloud": "the cloud vision model; the unredacted image was sent to the provider",
}
TESSERACT_HINT = "Install it with `brew install tesseract tesseract-lang` (macOS) or `apt install tesseract-ocr tesseract-ocr-deu`."


class ReaderError(RuntimeError):
    pass


def is_local_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if host in ("localhost", "host.docker.internal") or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


# --------------------------------------------------------------------------- Tesseract


def tesseract_languages(binary: str) -> set[str]:
    out = subprocess.run([binary, "--list-langs"], capture_output=True, text=True, timeout=30)
    return {line.strip() for line in out.stdout.splitlines()[1:] if line.strip()}


def ocr_reader(binary: str | None = None) -> ImageReader:
    binary = binary or shutil.which("tesseract")
    if not binary:
        raise ReaderError(f"Reading scans locally needs Tesseract OCR. {TESSERACT_HINT}")
    langs = tesseract_languages(binary)
    lang = "deu+eng" if "deu" in langs else "eng"

    def read(images: list[Image], trace: list[TraceEvent]) -> str:
        pages = []
        for n, img in enumerate(images, 1):
            _report(trace, "started", "ocr", n)
            t0 = time.perf_counter()
            # --psm 4: a single column of text of variable sizes, which suits letters better than full auto layout
            res = subprocess.run(
                [binary, "stdin", "stdout", "-l", lang, "--psm", "4"], input=img.data, capture_output=True, timeout=120
            )
            if res.returncode != 0:
                raise ReaderError(f"Tesseract could not read page {n}: {res.stderr.decode(errors='replace')[:300]}")
            text = res.stdout.decode("utf-8", errors="replace")
            event = TraceEvent(
                agent="ocr",
                model=f"tesseract ({lang})",
                attempt=n,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                note=None if lang != "eng" else "German language data missing: umlauts and ß may be misread. "
                "Install tesseract-lang for better results.",
            )
            trace.append(event)
            _report(trace, "decided", event)
            if event.note:
                _report(trace, "problem", event)
            pages.append(text.strip())
        return "\n\n".join(pages)

    return read


# --------------------------------------------------------------------------- vision models


def vision_reader(router: LLMRouter) -> ImageReader:
    def read(images: list[Image], trace: list[TraceEvent]) -> str:
        return TranscriberAgent(router).run(images, trace)

    return read


def build_image_reader(settings: Settings, router: LLMRouter | None) -> ImageReader:
    mode = settings.image_reader
    if mode == "ocr":
        return ocr_reader()
    if mode == "local_vision":
        if not is_local_url(settings.vision_base_url):
            raise ReaderError(
                f"BRIEFKASTEN_IMAGE_READER=local_vision but {settings.vision_base_url} is not a local address. "
                "Use a local server (e.g. Ollama) or set BRIEFKASTEN_IMAGE_READER=cloud deliberately."
            )
        from .llm.openai_compat import OpenAICompatLLM

        local = OpenAICompatLLM(settings.model_vision_local, settings.vision_base_url)
        return vision_reader(LLMRouter(smart=local, fast=local, vision=local))
    if mode == "cloud":
        if router is None:
            raise LLMError("Reading images with the cloud model needs a model provider.")
        return vision_reader(router)
    raise ReaderError(f"Unknown BRIEFKASTEN_IMAGE_READER '{mode}'. Use one of: {', '.join(READERS)}")
