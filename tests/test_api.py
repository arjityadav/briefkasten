import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BRIEFKASTEN_DATA_DIR", str(tmp_path))
    from briefkasten import api

    api.settings.cache_clear()
    return TestClient(api.app)


def test_index_and_meta(client):
    assert "Briefkasten" in client.get("/").text
    meta = client.get("/api/meta").json()
    assert "BW" in meta["states"] and "file_objection" in meta["intents"]


def test_demo_approve_export_flow(client):
    run = client.post("/api/demo/finanzamt_bescheid").json()
    assert run["status"] == "awaiting_approval"
    assert "redactions" not in run and run["redaction_count"] == 5

    blocked = client.post(f"/api/runs/{run['run_id']}/approve", json={})
    assert blocked.status_code == 409

    body = run["draft"]["body_de"].replace("[Nachweis beifügen, z. B. Bestätigung des Arbeitgebers]", "Bestätigung anbei.")
    ok = client.post(f"/api/runs/{run['run_id']}/approve", json={"body_de": body})
    assert ok.status_code == 200 and ok.json()["draft"]["status"] == "approved"

    letter = client.get(f"/api/runs/{run['run_id']}/letter")
    assert letter.status_code == 200 and "99/815/08154" in letter.text

    listed = client.get("/api/runs").json()
    assert listed[0]["run_id"] == run["run_id"]


def test_errors(client):
    assert client.get("/api/runs/../../etc/passwd").status_code == 404
    assert client.get("/api/runs/20260101-000000-abcdef").status_code == 404
    assert client.post("/api/demo/nope").status_code == 404


# --------------------------------------------------------------------------- public demo mode


@pytest.fixture
def demo_client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("BRIEFKASTEN_DATA_DIR", str(data))
    monkeypatch.setenv("BRIEFKASTEN_DEMO_ONLY", "1")
    monkeypatch.setenv("BRIEFKASTEN_IMPRINT_URL", "https://example.org/impressum")
    monkeypatch.setenv("BRIEFKASTEN_PRIVACY_URL", "https://example.org/datenschutz")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr("briefkasten.config.load_dotenv", lambda *a, **k: None)
    from briefkasten import api

    api.settings.cache_clear()
    api._memory_store.cache_clear()
    yield TestClient(api.app), data
    api.settings.cache_clear()
    api._memory_store.cache_clear()


def test_demo_only_refuses_real_letters(demo_client):
    client, data = demo_client
    r = client.post("/api/runs", data={"text": "Sehr geehrter Herr Mustermann, Ihre IBAN DE12 ..."})
    assert r.status_code == 403 and "sample letters" in r.json()["detail"]
    r = client.post("/api/runs", files={"file": ("brief.pdf", b"%PDF-1.4 real letter", "application/pdf")})
    assert r.status_code == 403
    assert not data.exists()  # nothing was written to disk


def test_demo_only_full_flow_without_key_or_disk(demo_client):
    client, data = demo_client
    meta = client.get("/api/meta").json()
    assert meta["demo_only"] and meta["models"] == {} and meta["imprint_url"].endswith("/impressum")

    run = client.post("/api/demo/finanzamt_bescheid").json()
    assert run["status"] == "awaiting_approval"
    body = run["draft"]["body_de"].replace("[Nachweis beifügen, z. B. Bestätigung des Arbeitgebers]", "Bestätigung anbei.")
    assert client.post(f"/api/runs/{run['run_id']}/approve", json={"body_de": body}).status_code == 200
    assert client.get(f"/api/runs/{run['run_id']}/letter").status_code == 200

    assert client.get("/api/runs").json() == []  # visitors never see each other's runs
    assert not data.exists()


def test_memory_store_expires_runs():
    from briefkasten.store import MemoryRunStore, RunNotFound

    from briefkasten.models import RunState
    from datetime import date, datetime

    store = MemoryRunStore(ttl_seconds=0, max_runs=10)
    st = RunState(run_id="20261028-000000-abcdef", created_at=datetime.now(), source_name="x", bundesland="BW", today=date.today(), text_redacted="x")
    store.save(st)
    with pytest.raises(RunNotFound):
        store.load(st.run_id)


def test_serve_refuses_public_full_app(monkeypatch):
    from click.testing import CliRunner

    from briefkasten.cli import main

    monkeypatch.setattr("briefkasten.config.load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("BRIEFKASTEN_DEMO_ONLY", raising=False)
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: calls.append(k))

    r = CliRunner().invoke(main, ["serve", "--host", "0.0.0.0"])
    assert r.exit_code != 0 and "Refusing" in r.output and not calls

    monkeypatch.setenv("BRIEFKASTEN_DEMO_ONLY", "1")
    r = CliRunner().invoke(main, ["serve", "--host", "0.0.0.0"])
    assert r.exit_code == 0 and calls and "Impressum" in r.output

    assert CliRunner().invoke(main, ["serve"]).exit_code == 0  # localhost is always fine


# --------------------------------------------------------------------------- live jobs (SSE)


def _events(client, job_id):
    import json

    with client.stream("GET", f"/api/jobs/{job_id}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def test_demo_job_streams_every_agent_step_then_the_run(client):
    job = client.post("/api/demo/vermieter_nebenkosten/job?pace=0").json()
    events = _events(client, job["job_id"])
    types = [e["type"] for e in events]
    assert types[0] == "info" and "Redactor masked" in events[0]["text"]
    assert types[-1] == "done" and events[-1]["run"]["status"] == "done"
    decided = [e for e in events if e["type"] == "decided"]
    assert decided[0]["agent"] == "supervisor" and decided[0]["tools"] == ["triage_letter"]
    assert any(e["type"] == "problem" and e["agent"] == "extractor" for e in events)  # the verifier's rejection
    assert len(decided) == len(events[-1]["run"]["trace"])
    assert all(b["t"] >= a["t"] for a, b in zip(events, events[1:]))


def test_job_events_can_resume_and_unknown_jobs_404(client):
    job = client.post("/api/demo/finanzamt_bescheid/job?pace=0").json()
    all_events = _events(client, job["job_id"])
    with client.stream("GET", f"/api/jobs/{job['job_id']}/events", headers={"last-event-id": "2"}) as r:
        resumed = [line for line in r.iter_lines() if line.startswith("data: ")]
    assert len(resumed) == len(all_events) - 3
    assert client.get("/api/jobs/nope/events").status_code == 404


def test_job_errors_are_reported_in_the_stream(client, monkeypatch):
    from briefkasten import api

    monkeypatch.setattr(api, "pipeline", lambda: _Broken())
    job = client.post("/api/jobs", data={"text": "Sehr geehrte Damen und Herren"}).json()
    events = _events(client, job["job_id"])
    assert events[-1]["type"] == "error" and "boom" in events[-1]["detail"]


class _Broken:
    settings = None

    def process(self, *a, **k):
        raise RuntimeError("boom")


def test_demo_only_refuses_job_uploads(demo_client):
    client, _ = demo_client
    assert client.post("/api/jobs", data={"text": "Hallo"}).status_code == 403
    assert client.post("/api/demo/handyvertrag/job?pace=0").status_code == 200


def test_job_status_for_polling_includes_calendar_and_summary(client):
    job = client.post("/api/demo/finanzamt_bescheid/job?pace=0").json()
    _events(client, job["job_id"])  # wait until finished
    s = client.get(f"/api/jobs/{job['job_id']}").json()
    assert s["done"] and s["status"] == "done" and s["steps"] == len(s["run"]["trace"])
    assert [(e["date"], e["basis"]) for e in s["calendar_events"]] == [("2026-11-16", "stated"), ("2026-11-19", "computed")]
    assert "Finanzamt Musterstadt" in s["calendar_events"][0]["title"]
    assert "Deadlines:" in s["summary_text"] and "waiting for your approval" in s["summary_text"]
    assert "99/815/08154" not in str(s)  # same redacted view as everything else
    assert client.get("/api/jobs/nope").status_code == 404


def test_failed_run_summary_keeps_partial_results():
    from briefkasten.notify import calendar_events, sender_label, summary_text

    run = {
        "run_id": "r1", "source_name": "mahnbescheid.txt", "status": "failed", "error": "AgentError: drafter did not finish",
        "triage": {"sender_name": None, "doc_type": "court_document"},
        "deadlines": [{"purpose": "Object to the Mahnbescheid", "due_date": "2026-11-17", "basis": "estimated", "steps": ["…"]}],
        "extraction": {"amounts": [{"value": 335.62, "purpose": "total claimed", "direction": "you_pay"}]},
    }
    assert sender_label(run) == "court document"
    text = summary_text(run)
    assert "stopped early" in text and "Tue 17.11.2026: Object to the Mahnbescheid (estimated)" in text and "335.62" in text
    assert calendar_events(run)[0]["title"] == "Object to the Mahnbescheid (court document)"
