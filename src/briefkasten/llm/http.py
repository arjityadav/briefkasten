"""Tiny JSON-over-HTTP client with retries (stdlib only, so the core has no HTTP dependency)."""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from functools import lru_cache

from .base import LLMError

RETRYABLE = {408, 409, 429, 500, 502, 503, 504, 529}


@lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    """Prefer certifi's CA bundle: python.org builds on macOS ship without root certificates."""
    try:
        import certifi
    except ImportError:  # pragma: no cover
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def post_json(url: str, payload: dict, headers: dict[str, str], *, timeout: int = 180, retries: int = 3) -> dict:
    body = json.dumps(payload).encode("utf-8")
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            # Cloudflare-fronted APIs (e.g. Groq) reject urllib's default User-Agent.
            headers={"content-type": "application/json", "user-agent": "briefkasten/0.1", **headers},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:2000]
            if e.code in RETRYABLE and attempt < retries:
                time.sleep(_retry_delay(e.headers.get("retry-after"), attempt))
                continue
            raise LLMError(f"HTTP {e.code} from {url}: {detail}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                time.sleep(2**attempt)
                continue
            raise LLMError(f"Could not reach {url}: {getattr(e, 'reason', e)}") from e
    raise LLMError(f"Giving up on {url}")  # pragma: no cover


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    """Honour the server's Retry-After (rate limits) up to a minute, else back off exponentially."""
    try:
        return min(max(float(retry_after), 0.5), 60.0)
    except (TypeError, ValueError):
        return min(2 ** (attempt + 1), 20)
