"""Tracing: the shape of a run's trace, what is kept out of it, and the real Langfuse SDK offline."""

import threading
from contextlib import contextmanager
from datetime import date

import pytest

from briefkasten import observability
from briefkasten.config import Settings
from briefkasten.demo import DEMO_REDACT_TERMS, load_replay
from briefkasten.observability import Observation, Observer, build_observer, mask_identifiers
from briefkasten.orchestrator import Briefkasten

TODAY = date(2026, 10, 28)


class Node(Observation):
    def __init__(self, name, kind, parent, **fields):
        self.name, self.kind, self.parent, self.fields = name, kind, parent, dict(fields)
        self.children, self.events, self.scores = [], [], {}
        self.trace_id = parent.trace_id if parent else "trace-1"

    def update(self, **fields):
        self.fields.update(fields)

    def event(self, name, *, level="DEFAULT", message=None, metadata=None):
        self.events.append((name, level))

    def score_trace(self, name, value, comment=None):
        self.scores[name] = value

    def find(self, name):
        return [n for n in self.walk() if n.name == name]

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


class RecordingObserver(Observer):
    """Builds the observation tree in memory, following the same context rules as Langfuse."""

    enabled = True

    def __init__(self):
        from contextvars import ContextVar

        self.roots: list[Node] = []
        self._stack: ContextVar[tuple[Node, ...]] = ContextVar("stack", default=())
        self._lock = threading.Lock()

    @contextmanager
    def _node(self, name, kind, **fields):
        stack = self._stack.get()
        node = Node(name, kind, stack[-1] if stack else None, **fields)
        with self._lock:
            (stack[-1].children if stack else self.roots).append(node)
        token = self._stack.set(stack + (node,))
        try:
            yield node
        finally:
            self._stack.reset(token)

    @contextmanager
    def _start_run(self, name, *, input, session_id, tags, metadata):
        with self._node("run", "agent", trace_name=name, session_id=session_id, tags=tags) as n:
            yield n

    @contextmanager
    def observe(self, name, *, kind="span", input=None, metadata=None, model=None):
        with self._node(name, kind, input=input, metadata=metadata, model=model) as n:
            yield n

    def trace_url(self, trace_id):
        return f"https://langfuse.example/trace/{trace_id}"


@pytest.fixture
def rec():
    r = RecordingObserver()
    observability.set_observer(r)
    return r


def _run(tmp_path, name="finanzamt_bescheid", **kw):
    letter, intent, router = load_replay(name)
    bk = Briefkasten(router, Settings(data_dir=tmp_path, redact_terms=DEMO_REDACT_TERMS))
    return bk.process(letter, reply_intent=intent, today=TODAY, **kw), router


def test_one_nested_trace_per_run(tmp_path, rec):
    state, _ = _run(tmp_path, session_id="eval-1")
    assert len(rec.roots) == 1
    root = rec.roots[0]
    assert root.fields["session_id"] == "eval-1" and "replay" in root.fields["tags"]
    assert state.trace_id == "trace-1" and state.trace_url.endswith("/trace-1")

    (sup,) = root.children
    assert (sup.name, sup.kind) == ("supervisor", "agent")
    # supervisor steps are generations; delegations are tools containing the specialist agent
    assert [c.kind for c in sup.children if c.name.startswith("supervisor step")] == ["generation"] * 5
    (triage_tool,) = sup.find("triage_letter")
    assert triage_tool.kind == "tool" and [c.name for c in triage_tool.children] == ["triage"]
    gen = sup.find("supervisor step 1")[0]
    assert gen.fields["usage_details"]["input"] > 0 and gen.fields["output"]["tool_calls"][0]["name"] == "triage_letter"


def test_parallel_delegations_keep_their_parents(tmp_path, rec):
    _run(tmp_path)
    sup = rec.roots[0].children[0]
    explain, extract = sup.find("explain_letter")[0], sup.find("extract_facts")[0]
    assert [c.name for c in explain.children] == ["explainer"]  # ran in a worker thread, still nested
    assert [c.name for c in extract.children] == ["extractor"]


def test_rejections_are_warnings_and_scores_summarise_the_run(tmp_path, rec):
    state, _ = _run(tmp_path, "vermieter_nebenkosten")
    root = rec.roots[0]
    rejected = [n for n in root.walk() if n.kind == "tool" and n.name == "submit" and n.fields.get("level") == "WARNING"]
    assert rejected and "283.47" in rejected[0].fields["output"]
    assert root.scores["checks_passed"] == 1.0 and root.scores["repaired"] == len(state.verification.repaired) == 2
    assert root.scores["agent_steps"] == len(state.trace) and root.scores["status"] == "done"
    assert root.fields["metadata"]["masked"]["PERSONAL"] >= 1


def test_raw_identifiers_and_scans_stay_out_of_the_trace(tmp_path, rec, monkeypatch):
    from test_documents import _fake_tesseract, _png

    monkeypatch.setattr("shutil.which", lambda _: _fake_tesseract(tmp_path))
    letter, intent, router = load_replay("finanzamt_bescheid")
    bk = Briefkasten(router, Settings(data_dir=tmp_path / "d", redact_terms=DEMO_REDACT_TERMS))
    bk.process_files([(_png(1), "scan.png")], today=TODAY)
    (root,) = rec.roots  # OCR intake and processing share one trace
    ocr = root.find("ocr")[0]
    assert ocr.fields.get("input") is None and ocr.fields.get("output") is None  # metadata only
    everything = repr([(n.fields) for n in root.walk()])
    for secret in ("DE12 6005", "99/815/08154", "Max Mustermann"):
        assert secret not in everything


def test_transcriber_output_is_not_logged(rec):
    from briefkasten.llm import Image, LLMRouter, ScriptedLLM
    from briefkasten.readers import vision_reader

    llm = ScriptedLLM({"transcriber": ["Max Mustermann, IBAN DE12 6005 0101"]})
    vision_reader(LLMRouter(llm, llm))([Image("image/png", b"x")], [])
    (gen,) = rec.roots
    assert gen.fields["input"] == {"images": 1, "prompt": "Transcribe this letter."}
    assert "not logged" in gen.fields["output"]


def test_mask_is_a_second_layer():
    masked = mask_identifiers(data={"a": ["IBAN DE12 6005 0101 0001 2345 67", {"tel": "0711 123456"}], "n": 3})
    assert masked == {"a": ["IBAN [IBAN]", {"tel": "[PHONE]"}], "n": 3}


def test_tracing_switches(monkeypatch):
    assert not build_observer({}).enabled
    assert not build_observer({"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk", "BRIEFKASTEN_TRACING": "off"}).enabled


# --------------------------------------------------------------------------- the real SDK, offline


def test_real_langfuse_sdk_exports_the_nested_trace(tmp_path):
    pytest.importorskip("langfuse")
    from langfuse import Langfuse
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from briefkasten.observability import LangfuseObserver

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    client = Langfuse(
        public_key="pk-test", secret_key="sk-test", base_url="http://127.0.0.1:9", tracer_provider=provider,
        span_exporter=exporter, mask=mask_identifiers, flush_at=1,
    )
    scores = {}
    client.create_score = lambda **kw: scores.__setitem__(kw["name"], kw["value"])  # the score API needs a server
    client.get_trace_url = lambda trace_id=None: f"http://langfuse.local/trace/{trace_id}"
    observability.set_observer(LangfuseObserver(client))
    try:
        state, _ = _run(tmp_path)
        provider.force_flush()
    finally:
        observability.set_observer(Observer())
    spans = exporter.get_finished_spans()
    by_id = {s.context.span_id: s for s in spans}
    names = [s.name for s in spans]
    assert {"run", "supervisor", "triage", "extractor", "supervisor step 1", "triage_letter"} <= set(names)
    assert len({s.context.trace_id for s in spans}) == 1  # one trace for the whole run
    assert state.trace_id == format(spans[0].context.trace_id, "032x")
    triage = next(s for s in spans if s.name == "triage")
    assert by_id[triage.parent.span_id].name == "triage_letter"
    extractor = next(s for s in spans if s.name == "extractor")  # parallel branch, other thread
    assert by_id[extractor.parent.span_id].name == "extract_facts"
    attrs = repr([dict(s.attributes) for s in spans])
    assert "langfuse.observation.type" in attrs and "generation" in attrs
    assert "99/815/08154" not in attrs and "DE12 6005" not in attrs
    assert scores["checks_passed"] == 1.0 and scores["agent_steps"] == len(state.trace)
    assert state.trace_url == f"http://langfuse.local/trace/{state.trace_id}"
