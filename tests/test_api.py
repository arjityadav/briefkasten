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
