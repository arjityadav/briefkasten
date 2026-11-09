"""Tiny JSON-over-HTTP client with retries (stdlib only, so the core has no HTTP dependency)."""

from __future__ import annotations

import json
import logging
import re
import ssl
import threading
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
MIN_OUTPUT_TOKENS = 256


class TokenPacer:
    """Keeps requests inside the provider's per-minute token budget instead of running into 429s.

    Driven by the rate-limit headers the provider sends with every response (Groq, OpenAI):
    nothing to configure, and providers without them are never paced. Shared by the threads of
    parallel agents, so their requests queue up for the budget together.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.limit: int | None = None
        self._remaining = 0
        self._reset_at = 0.0

    def fit(self, payload: dict, body_chars: int) -> int:
        """Lower `max_tokens` so one request never exceeds the per-minute limit; return the request's cost."""
        prompt = int(body_chars / 3.5)  # rough token count of the request body, on the safe side
        if self.limit and "max_tokens" in payload:
            room = self.limit - prompt - 200
            if room < MIN_OUTPUT_TOKENS:
                raise LLMError(
                    f"Request of about {prompt} tokens doesn't fit the provider's limit of {self.limit} tokens per minute."
                )
            payload["max_tokens"] = min(payload["max_tokens"], room)
        return prompt + payload.get("max_tokens", 0)

    def wait(self, cost: int) -> None:
        if not self.limit:
            return
        with self._lock:  # held while sleeping, so parallel requests take turns
            now = time.monotonic()
            if now >= self._reset_at:
                self._remaining = self.limit
            elif cost > self._remaining:
                # The budget refills continuously (limit per 60 s): wait only for the missing part.
                delay = min(self._reset_at - now, (cost - self._remaining) * 60 / self.limit)
                log.warning("pacing: waiting %.0fs for the per-minute token budget (%d tokens free, about %d needed)", delay, max(self._remaining, 0), cost)
                time.sleep(delay)
                self._remaining = self.limit
            self._remaining -= cost

    def update(self, headers) -> None:
        if headers is None:
            return
        limit, remaining = headers.get("x-ratelimit-limit-tokens"), headers.get("x-ratelimit-remaining-tokens")
        reset = _duration(headers.get("x-ratelimit-reset-tokens"))
        if not (limit and remaining and reset is not None):
            return
        with self._lock:
            self.limit, self._remaining = int(limit), int(remaining)
            self._reset_at = time.monotonic() + reset


_pacers: dict[str, TokenPacer] = {}


def pacer(url: str) -> TokenPacer:
    host = url.split("/")[2] if "//" in url else url
    return _pacers.setdefault(host, TokenPacer())


def _duration(value: str | None) -> float | None:
    """Parse rate-limit durations like '7.66s', '134ms' or '1m26.4s'."""
    if not value:
        return None
    parts = re.findall(r"([\d.]+)(ms|h|m|s)", value)
    if not parts:
        return None
    unit = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
    return sum(float(n) * unit[u] for n, u in parts)


def post_json(url: str, payload: dict, headers: dict[str, str], *, timeout: int = 180, retries: int = 3) -> dict:
    budget = pacer(url)
    payload = dict(payload)
    # Inline images (base64) are billed by size in pixels, not characters: count each as a flat 1,500.
    text = json.dumps(payload)
    images = len(re.findall(r'"data:[^"]*"', text))
    cost = budget.fit(payload, len(re.sub(r'"data:[^"]*"', '""', text)) + images * 1500 * 3.5)
    body = json.dumps(payload).encode("utf-8")
    attempt = rate_limited = 0
    while True:
        budget.wait(cost)
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            # Cloudflare-fronted APIs (e.g. Groq) reject urllib's default User-Agent.
            headers={"content-type": "application/json", "user-agent": "briefkasten/0.1", **headers},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
                budget.update(getattr(resp, "headers", None))
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            budget.update(e.headers)
            detail = e.read().decode("utf-8", errors="replace")[:2000]
            if e.code == 429 and _daily_quota(detail):
                # Waiting a minute can't help with a per-day quota: stop now and say so.
                raise LLMError(f"Daily token quota of the model provider is used up. {_quota_message(detail)}") from e
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


def _daily_quota(detail: str) -> bool:
    d = detail.lower()
    return "per day" in d or "(tpd)" in d or "(rpd)" in d


def _quota_message(detail: str) -> str:
    try:
        return json.loads(detail)["error"]["message"][:400]
    except (ValueError, KeyError, TypeError):
        return detail[:400]


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    """Honour the server's Retry-After (rate limits) up to a minute, else back off exponentially."""
    try:
        return min(max(float(retry_after), 0.5), 60.0)
    except (TypeError, ValueError):
        return min(2 ** (attempt + 1), 20)
