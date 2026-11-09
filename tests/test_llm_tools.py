"""Tool-calling wire formats for both providers, with the HTTP call stubbed out."""

from briefkasten.llm import Message, ToolCall, ToolResult, ToolSpec
from briefkasten.llm import anthropic as anthropic_mod
from briefkasten.llm import openai_compat as openai_mod

TOOLS = [ToolSpec("find_in_letter", "Search the letter", {"type": "object", "properties": {"text": {"type": "string"}}})]
HISTORY = [
    Message("user", text="<letter>...</letter>"),
    Message("assistant", text="Let me check.", tool_calls=[ToolCall("c1", "find_in_letter", {"text": "412,00"})]),
    Message("tool", results=[ToolResult("c1", "FOUND verbatim in the letter."), ]),
]


def test_anthropic_tool_round_trip(monkeypatch):
    sent = {}

    def fake_post(url, payload, headers, **kw):
        sent.update(payload)
        return {
            "model": "claude-test",
            "content": [
                {"type": "text", "text": "Found it."},
                {"type": "tool_use", "id": "c2", "name": "submit", "input": {"x": 1}},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

    monkeypatch.setattr(anthropic_mod, "post_json", fake_post)
    res = anthropic_mod.AnthropicLLM("claude-test", "key").chat(system="s", messages=HISTORY, tools=TOOLS, agent="extractor")

    assert sent["tools"][0]["input_schema"]["properties"]["text"]["type"] == "string"
    assistant, tool = sent["messages"][1], sent["messages"][2]
    assert assistant["content"][1] == {"type": "tool_use", "id": "c1", "name": "find_in_letter", "input": {"text": "412,00"}}
    assert tool["role"] == "user" and tool["content"][0]["type"] == "tool_result" and tool["content"][0]["tool_use_id"] == "c1"
    assert res.text == "Found it." and res.tool_calls == [ToolCall("c2", "submit", {"x": 1})]


def test_openai_tool_round_trip(monkeypatch):
    sent = {}

    def fake_post(url, payload, headers, **kw):
        sent.update(payload)
        return {
            "model": "qwen",
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {"id": "c2", "type": "function", "function": {"name": "submit", "arguments": '{"x": 1}'}},
                            {"id": "c3", "type": "function", "function": {"name": "submit", "arguments": "{broken"}},
                        ],
                    }
                }
            ],
        }

    monkeypatch.setattr(openai_mod, "post_json", fake_post)
    res = openai_mod.OpenAICompatLLM("qwen", "http://x/v1").chat(system="s", messages=HISTORY, tools=TOOLS, agent="extractor")

    msgs = sent["messages"]
    assert msgs[0]["role"] == "system" and sent["tools"][0]["function"]["name"] == "find_in_letter"
    assert msgs[2]["tool_calls"][0]["function"]["arguments"] == '{"text": "412,00"}'
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "FOUND verbatim in the letter."}
    assert res.text == "" and res.tool_calls[0] == ToolCall("c2", "submit", {"x": 1})
    assert "_unparseable_arguments" in res.tool_calls[1].input  # reaches the model as a validation error


# --------------------------------------------------------------------------- Groq


def test_groq_router_uses_groq_defaults(monkeypatch):
    from briefkasten.config import Settings
    from briefkasten.llm import build_router

    for k in ("BRIEFKASTEN_MODEL", "BRIEFKASTEN_MODEL_FAST", "BRIEFKASTEN_MODEL_VISION"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BRIEFKASTEN_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setattr("briefkasten.config.load_dotenv", lambda *a, **k: None)
    router = build_router(Settings.from_env())
    assert {router.for_tier(t).model for t in ("smart", "fast", "vision")} == {"qwen/qwen3.8-27b"}
    assert router.smart._url == "https://api.groq.com/openai/v1/chat/completions"


def test_groq_requires_key():
    import pytest

    from briefkasten.config import Settings
    from briefkasten.llm import LLMError, build_router

    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        build_router(Settings(provider="groq", groq_api_key=None))


def test_groq_reasoning_settings_and_reasoning_channel(monkeypatch):
    sent = {}

    def fake_post(url, payload, headers, **kw):
        sent.update(payload)
        msg = {"content": "", "reasoning": "Need to search first.", "tool_calls": []}
        return {"model": "openai/gpt-oss-120b", "choices": [{"message": msg}]}

    monkeypatch.setattr(openai_mod, "post_json", fake_post)
    llm = openai_mod.OpenAICompatLLM("openai/gpt-oss-120b", openai_mod.GROQ_BASE_URL, "k", **openai_mod.groq_options("openai/gpt-oss-120b"))
    res = llm.chat(system="s", messages=HISTORY, tools=TOOLS, agent="triage", max_tokens=800)
    assert sent["reasoning_effort"] == "low" and sent["max_tokens"] == 800 + 2048
    assert res.reasoning == "Need to search first." and res.text == ""
    assert openai_mod.groq_options("qwen/qwen3.8-27b") == {"extra": {"reasoning_effort": "none"}}
    assert openai_mod.groq_options("llama-3.3-70b-versatile") == {}


def test_groq_malformed_tool_call_becomes_a_retry(monkeypatch):
    from briefkasten.llm import LLMError

    def fake_post(url, payload, headers, **kw):
        raise LLMError(
            'HTTP 400 from x: {"error":{"message":"Tool call validation failed: `/sender_type`: value must be one of '
            "'tax_office', 'other'\",\"type\":\"invalid_request_error\",\"code\":\"tool_use_failed\"}}"
        )

    monkeypatch.setattr(openai_mod, "post_json", fake_post)
    res = openai_mod.OpenAICompatLLM("m", "http://x/v1").chat(system="s", messages=HISTORY, tools=TOOLS, agent="a")
    assert res.tool_calls == [] and res.rejected.startswith("Tool call validation failed: `/sender_type`")


def test_other_http_errors_still_raise(monkeypatch):
    import pytest

    from briefkasten.llm import LLMError

    def fake_post(url, payload, headers, **kw):
        raise LLMError('HTTP 401 from x: {"error":{"message":"Invalid API Key"}}')

    monkeypatch.setattr(openai_mod, "post_json", fake_post)
    with pytest.raises(LLMError, match="Invalid API Key"):
        openai_mod.OpenAICompatLLM("m", "http://x/v1").chat(system="s", messages=HISTORY, tools=TOOLS, agent="a")


def test_retry_after_is_honoured():
    from briefkasten.llm.http import _retry_delay

    assert _retry_delay("7", 0) == 7.0
    assert _retry_delay("600", 0) == 60.0
    assert _retry_delay(None, 1) == 4


def test_groq_via_openai_variables(monkeypatch):
    """OPENAI_API_KEY + OPENAI_BASE_URL pointing at Groq, no BRIEFKASTEN_PROVIDER: treated as Groq."""
    from briefkasten.config import Settings

    for k in ("BRIEFKASTEN_PROVIDER", "BRIEFKASTEN_MODEL", "BRIEFKASTEN_MODEL_FAST", "BRIEFKASTEN_MODEL_VISION", "GROQ_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "gsk_test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("OPENAI_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setattr("briefkasten.config.load_dotenv", lambda *a, **k: None)
    s = Settings.from_env()
    assert (s.provider, s.groq_api_key, s.model, s.model_fast) == ("groq", "gsk_test", "openai/gpt-oss-120b", "qwen/qwen3.8-27b")


def test_openai_model_variable_for_local_servers(monkeypatch):
    from briefkasten.config import Settings

    for k in ("BRIEFKASTEN_MODEL", "BRIEFKASTEN_MODEL_FAST", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BRIEFKASTEN_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5:14b")
    monkeypatch.setattr("briefkasten.config.load_dotenv", lambda *a, **k: None)
    s = Settings.from_env()
    assert (s.provider, s.model, s.model_fast) == ("openai", "qwen2.5:14b", "qwen2.5:14b")


def test_rejection_message_survives_truncated_body():
    err = 'HTTP 400 from x: {"error":{"message":"Tool call validation failed: attempted to call tool \'json\'","type":"invalid_request_error","failed_generation":"{\\"summ'
    assert openai_mod._tool_call_rejection(err) == "Tool call validation failed: attempted to call tool 'json'"


def test_rate_limit_waits_are_logged(monkeypatch, caplog):
    import io
    import urllib.error

    from briefkasten.llm import http

    calls = {"n": 0}

    def fake_urlopen(req, timeout, context):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {"retry-after": "0.5"}, io.BytesIO(b"{}"))
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    logger = http.log
    monkeypatch.setattr(logger, "propagate", True)
    with caplog.at_level("WARNING", logger="briefkasten.http"):
        assert http.post_json("https://x", {}, {}) == {"ok": True}
    assert "rate limited, retrying in" in caplog.text and "wait 1 of 8" in caplog.text


def test_rate_limits_have_their_own_longer_budget(monkeypatch):
    import io
    import urllib.error

    from briefkasten.llm import http

    calls = {"n": 0}

    def fake_urlopen(req, timeout, context):
        calls["n"] += 1
        if calls["n"] <= 6:  # more 429s than the normal retry budget of 3
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {"retry-after": "1"}, io.BytesIO(b"{}"))
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    assert http.post_json("https://x", {}, {}) == {"ok": True} and calls["n"] == 7


def test_daily_quota_fails_fast_instead_of_waiting(monkeypatch):
    import io
    import urllib.error

    import pytest

    from briefkasten.llm import LLMError, http

    body = b'{"error":{"message":"Rate limit reached for model `qwen/qwen3.8-27b` on tokens per day (TPD): Limit 200000, Used 196897. Please try again in 4m55s.","code":"rate_limit_exceeded"}}'
    calls, sleeps = {"n": 0}, []

    def fake_urlopen(req, timeout, context):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {"retry-after": "295"}, io.BytesIO(body))

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    with pytest.raises(LLMError, match="Daily token quota.*tokens per day"):
        http.post_json("https://x", {}, {})
    assert calls["n"] == 1 and not sleeps


def _groq_headers(remaining: int, reset: str = "30s", limit: int = 8000) -> dict:
    return {"x-ratelimit-limit-tokens": str(limit), "x-ratelimit-remaining-tokens": str(remaining), "x-ratelimit-reset-tokens": reset}


def _fake_response(headers: dict):
    import io

    class Resp(io.BytesIO):
        def __init__(self):
            super().__init__(b'{"ok": true}')
            self.headers = headers

    return Resp()


def test_rate_limit_durations_parse():
    from briefkasten.llm.http import _duration

    assert _duration("134ms") == 0.134
    assert _duration("7.66s") == 7.66
    assert abs(_duration("1m26.4s") - 86.4) < 1e-9
    assert _duration(None) is None and _duration("soon") is None


def test_requests_wait_for_the_per_minute_token_budget(monkeypatch, caplog):
    from briefkasten.llm import http

    monkeypatch.setitem(http._pacers, "paced.example", http.TokenPacer())
    sleeps: list[float] = []
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda req, timeout, context: _fake_response(_groq_headers(300)))
    monkeypatch.setattr(http.log, "propagate", True)

    http.post_json("https://paced.example/v1", {"max_tokens": 100}, {})  # learns: 300 tokens left for 30 s
    assert not sleeps
    with caplog.at_level("WARNING", logger="briefkasten.http"):
        http.post_json("https://paced.example/v1", {"max_tokens": 1000}, {})  # doesn't fit: waits for the reset
    assert len(sleeps) == 1 and 5 < sleeps[0] < 6  # about 700 tokens missing, refilled at 8,000 a minute
    assert "pacing: waiting" in caplog.text


def test_providers_without_rate_limit_headers_are_never_paced(monkeypatch):
    from briefkasten.llm import http

    monkeypatch.setitem(http._pacers, "local.example", http.TokenPacer())
    sleeps: list[float] = []
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda req, timeout, context: _fake_response({}))
    for _ in range(3):
        http.post_json("https://local.example/v1", {"max_tokens": 50_000}, {})
    assert not sleeps


def test_output_is_capped_so_one_request_fits_the_limit():
    import pytest

    from briefkasten.llm import LLMError
    from briefkasten.llm.http import TokenPacer

    p = TokenPacer()
    p.update(_groq_headers(8000))
    payload = {"max_tokens": 4000, "messages": "x" * 14_000}  # about 4,000 prompt tokens
    p.fit(payload, len(str(payload)))
    assert payload["max_tokens"] < 4000 and 4000 + payload["max_tokens"] <= 8000
    with pytest.raises(LLMError, match="doesn't fit"):
        p.fit({"max_tokens": 500}, 30_000)


def test_inline_images_do_not_count_as_text(monkeypatch):
    from briefkasten.llm import http

    monkeypatch.setitem(http._pacers, "vision.example", http.TokenPacer())
    http._pacers["vision.example"].update(_groq_headers(8000))
    sent = {}

    def fake_urlopen(req, timeout, context):
        sent["body"] = req.data
        return _fake_response({})

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    image = "data:image/png;base64," + "A" * 200_000  # far more than 8,000 tokens as text
    http.post_json("https://vision.example/v1", {"max_tokens": 2000, "messages": [{"url": image}]}, {})
    assert b'"max_tokens": 2000' in sent["body"]


def test_nvidia_has_its_own_credentials(monkeypatch):
    from briefkasten.config import Settings
    from briefkasten.llm import build_router

    monkeypatch.setattr("briefkasten.config.load_dotenv", lambda: None)
    for k in ("BRIEFKASTEN_MODEL", "BRIEFKASTEN_MODEL_FAST", "NVIDIA_MODEL", "NVIDIA_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BRIEFKASTEN_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_KEY", "nvapi-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")  # set up for Groq: must not leak in
    monkeypatch.setenv("OPENAI_MODEL", "qwen/qwen3.8-27b")
    s = Settings.from_env()
    assert s.provider == "nvidia" and s.nvidia_api_key == "nvapi-test"
    assert s.model == s.model_fast == "mistralai/mistral-large-4-1t-a52b"
    smart = build_router(s).for_tier("smart")
    assert smart._url == "https://integrate.api.nvidia.com/v1/chat/completions" and smart._key == "nvapi-test"


def test_pacing_waits_only_for_the_missing_tokens(monkeypatch):
    from briefkasten.llm import http

    p = http.TokenPacer()
    p.update(_groq_headers(1000, reset="50s"))  # 8,000 per minute refill at about 133 a second
    sleeps: list[float] = []
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    p.wait(2000)
    assert 7 < sleeps[0] < 8  # 1,000 missing tokens, not the full 50 s
