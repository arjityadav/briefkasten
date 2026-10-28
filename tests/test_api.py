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
