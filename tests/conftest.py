import pytest

from briefkasten import observability


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch):
    """Tests never send traces, even if the developer's .env has Langfuse keys."""
    monkeypatch.setenv("BRIEFKASTEN_TRACING", "off")
    observability.set_observer(observability.Observer())
    yield
    observability.set_observer(None)
