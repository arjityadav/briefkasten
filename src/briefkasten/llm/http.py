"""Tiny JSON-over-HTTP client with retries (stdlib only, so the core has no HTTP dependency)."""

from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from functools import lru_cache

from .base import LLMError

log = logging.getLogger("briefkasten.http")

RETRYABLE = {408, 409, 429, 500, 502, 503, 504, 529}


@lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    """Prefer certifi's CA bundle: python.org builds on macOS ship without root certificates."""
    try:
        import certifi
    except ImportError:  # pragma: no cover
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


RATE_LIMIT_RETRIES = 8  # 429s are safe to wait out; per-minute token limits clear within a minute or two


def post_json(url: str, payload: dict, headers: dict[str, str], *, timeout: int = 180, retries: int = 3) -> dict:
    body = json.dumps(payload).encode("utf-8")
    attempt = rate_limited = 0
    while True:
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
            if e.code == 429 and rate_limited < RATE_LIMIT_RETRIES:
                rate_limited += 1
                delay = _retry_delay(e.headers.get("retry-after"), rate_limited - 1)
                log.warning("rate limited, retrying in %.0fs (wait %d of %d)", delay, rate_limited, RATE_LIMIT_RETRIES)
                time.sleep(delay)
                continue
            if e.code in RETRYABLE and e.code != 429 and attempt < retries:
                attempt += 1
                delay = _retry_delay(e.headers.get("retry-after"), attempt - 1)
                log.warning("HTTP %d, retrying in %.0fs (attempt %d of %d)", e.code, delay, attempt + 1, retries + 1)
                time.sleep(delay)
                continue
            raise LLMError(f"HTTP {e.code} from {url}: {detail}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                log.warning("connection problem (%s), retrying in %ds", getattr(e, "reason", e), 2**attempt)
                time.sleep(2**attempt)
                attempt += 1
                continue
            raise LLMError(f"Could not reach {url}: {getattr(e, 'reason', e)}") from e


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    """Honour the server's Retry-After (rate limits) up to a minute, else back off exponentially."""
    try:
        return min(max(float(retry_after), 0.5), 60.0)
    except (TypeError, ValueError):
        return min(2 ** (attempt + 1), 20)
