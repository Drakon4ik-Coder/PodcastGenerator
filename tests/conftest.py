import os
import json
import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# TTS mock helpers
# ---------------------------------------------------------------------------

def make_mock_pipeline():
    """Return a mock Kokoro pipeline that yields deterministic fake segments."""
    def _call(text, voice="af_heart", speed=1.0):
        for sent in (s.strip() for s in text.split(".") if s.strip()):
            # 0.1 s of silence at 24 kHz
            yield sent + ".", None, np.zeros(2400, dtype=np.float32)

    mock = MagicMock()
    mock.side_effect = _call
    return mock


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with an isolated temp database and audio directory."""
    db_path = str(tmp_path / "test.db")
    audio_dir = str(tmp_path / "audio")
    os.makedirs(audio_dir, exist_ok=True)

    monkeypatch.setenv("SECRET_KEY", "test-secret-key-not-for-production")
    monkeypatch.setattr("app.database.DB_PATH", db_path)
    monkeypatch.setattr("app.main.AUDIO_DIR", audio_dir)

    # Prevent the startup pre-warm thread from loading the real Kokoro model
    with patch("app.tts.get_pipeline", return_value=make_mock_pipeline()):
        from app.main import app
        from app.database import init_db

        init_db()

        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


@pytest.fixture()
def mock_pipeline(monkeypatch):
    """Patch get_pipeline with a fast, deterministic fake for TTS endpoint tests."""
    # Reset the cached singleton so our mock is used
    monkeypatch.setattr("app.tts._pipeline", None)
    mock = make_mock_pipeline()
    with patch("app.tts.get_pipeline", return_value=mock):
        yield mock


@pytest.fixture()
def registered_user(client):
    """Register a test user and return their credentials."""
    resp = client.post(
        "/register",
        data={"username": "alice", "password": "password123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return {"username": "alice", "password": "password123"}


@pytest.fixture()
def auth_client(client, registered_user):
    """A TestClient whose session cookie is authenticated as alice."""
    resp = client.post(
        "/login",
        data=registered_user,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "token" in client.cookies
    return client


# ---------------------------------------------------------------------------
# Helper utilities used across multiple test modules
# ---------------------------------------------------------------------------

def parse_sse(text: str) -> list[dict]:
    """Parse SSE event stream text into a list of decoded JSON payloads."""
    events = []
    for block in text.strip().split("\n\n"):
        block = block.strip()
        if block.startswith("data: "):
            try:
                events.append(json.loads(block[6:]))
            except json.JSONDecodeError:
                pass
    return events


def generate_audio(client, text: str = "Hello world. This is a test.") -> dict:
    """Call /api/tts/generate with a mock pipeline and return the done event."""
    with patch("app.tts.get_pipeline", return_value=make_mock_pipeline()):
        resp = client.post("/api/tts/generate", json={"text": text})
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    done = next(e for e in events if e["type"] == "done")
    return done
