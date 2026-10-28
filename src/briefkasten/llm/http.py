"""Tiny JSON-over-HTTP client with retries (stdlib only, so the core has no HTTP dependency)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .base import LLMError

RETRYABLE = {408, 409, 429, 500, 502, 503, 504, 529}


def post_json(url: str, payload: dict, headers: dict[str, str], *, timeout: int = 180, retries: int = 3) -> dict:
    body = json.dumps(payload).encode("utf-8")
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=body, method="POST", headers={"content-type": "application/json", **headers}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            if e.code in RETRYABLE and attempt < retries:
                time.sleep(min(2 ** (attempt + 1), 20))
                continue
            raise LLMError(f"HTTP {e.code} from {url}: {detail}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                time.sleep(2**attempt)
                continue
            raise LLMError(f"Could not reach {url}: {getattr(e, 'reason', e)}") from e
    raise LLMError(f"Giving up on {url}")  # pragma: no cover
