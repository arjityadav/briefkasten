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
    assert router.for_tier("smart").model == "openai/gpt-oss-120b"
    assert router.for_tier("fast").model == "openai/gpt-oss-20b"
    assert router.for_tier("vision").model == "qwen/qwen3.8-27b"
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
    assert (s.provider, s.groq_api_key, s.model, s.model_fast) == ("groq", "gsk_test", "openai/gpt-oss-120b", "openai/gpt-oss-20b")


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
