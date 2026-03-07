"""Integration tests for audio file serving, rename, and delete endpoints."""
import os
import pytest
from unittest.mock import patch
from tests.conftest import generate_audio, make_mock_pipeline


@pytest.fixture()
def saved_audio(auth_client, mock_pipeline):
    """Generate and persist a recording; return its audio_id."""
    done = generate_audio(auth_client)
    return done["audio_id"]


@pytest.fixture()
def second_user_client(client):
    """A second authenticated user with their own session."""
    client.post(
        "/register",
        data={"username": "eve", "password": "evepassword"},
        follow_redirects=False,
    )
    client.post(
        "/login",
        data={"username": "eve", "password": "evepassword"},
        follow_redirects=False,
    )
    return client


class TestServeAudio:
    def test_returns_wav_for_own_recording(self, auth_client, saved_audio):
        resp = auth_client.get(f"/api/audio/{saved_audio}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/wav"
        assert resp.content[:4] == b"RIFF"

    def test_requires_authentication(self, client, auth_client, saved_audio):
        # Use a fresh unauthenticated client
        from fastapi.testclient import TestClient
        from app.main import app
        fresh = TestClient(app, raise_server_exceptions=True)
        resp = fresh.get(f"/api/audio/{saved_audio}")
        assert resp.status_code == 401

    def test_returns_404_for_nonexistent_id(self, auth_client):
        resp = auth_client.get("/api/audio/99999")
        assert resp.status_code == 404

    def test_returns_404_for_other_users_recording(
        self, client, registered_user, mock_pipeline, tmp_path, monkeypatch
    ):
        """Alice's audio should not be accessible to Eve."""
        # Log in as alice
        client.post("/login", data=registered_user, follow_redirects=False)
        done = generate_audio(client)
        audio_id = done["audio_id"]

        # Switch session to Eve
        client.post(
            "/register",
            data={"username": "eve", "password": "evepassword"},
            follow_redirects=False,
        )
        client.post(
            "/login",
            data={"username": "eve", "password": "evepassword"},
            follow_redirects=False,
        )
        resp = client.get(f"/api/audio/{audio_id}")
        assert resp.status_code == 404


class TestRenameAudio:
    def test_rename_success(self, auth_client, saved_audio):
        resp = auth_client.patch(
            f"/api/audio/{saved_audio}/title",
            json={"title": "My favourite episode"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_renamed_title_persists(self, auth_client, saved_audio):
        auth_client.patch(
            f"/api/audio/{saved_audio}/title",
            json={"title": "Renamed"},
        )
        from app.database import get_db
        with get_db() as conn:
            af = conn.execute(
                "SELECT title FROM audio_files WHERE id = ?", (saved_audio,)
            ).fetchone()
        assert af["title"] == "Renamed"

    def test_empty_title_returns_400(self, auth_client, saved_audio):
        resp = auth_client.patch(
            f"/api/audio/{saved_audio}/title",
            json={"title": ""},
        )
        assert resp.status_code == 400

    def test_whitespace_title_returns_400(self, auth_client, saved_audio):
        resp = auth_client.patch(
            f"/api/audio/{saved_audio}/title",
            json={"title": "   "},
        )
        assert resp.status_code == 400

    def test_rename_nonexistent_returns_404(self, auth_client):
        resp = auth_client.patch(
            "/api/audio/99999/title",
            json={"title": "Anything"},
        )
        assert resp.status_code == 404

    def test_rename_requires_authentication(self, client, auth_client, saved_audio):
        from fastapi.testclient import TestClient
        from app.main import app
        fresh = TestClient(app, raise_server_exceptions=True)
        resp = fresh.patch(
            f"/api/audio/{saved_audio}/title", json={"title": "Hack"}
        )
        assert resp.status_code == 401


class TestDeleteAudio:
    def test_delete_success_returns_ok(self, auth_client, saved_audio):
        resp = auth_client.delete(f"/api/audio/{saved_audio}")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_deleted_recording_no_longer_fetchable(self, auth_client, saved_audio):
        auth_client.delete(f"/api/audio/{saved_audio}")
        resp = auth_client.get(f"/api/audio/{saved_audio}")
        assert resp.status_code == 404

    def test_delete_removes_file_from_disk(self, auth_client, saved_audio, tmp_path, monkeypatch):
        from app.database import get_db
        with get_db() as conn:
            af = conn.execute(
                "SELECT filename FROM audio_files WHERE id = ?", (saved_audio,)
            ).fetchone()
        from app import main as main_mod
        path = os.path.join(main_mod.AUDIO_DIR, af["filename"])

        assert os.path.exists(path), "file should exist before delete"
        auth_client.delete(f"/api/audio/{saved_audio}")
        assert not os.path.exists(path), "file should be removed after delete"

    def test_delete_removes_db_record(self, auth_client, saved_audio):
        auth_client.delete(f"/api/audio/{saved_audio}")
        from app.database import get_db
        with get_db() as conn:
            af = conn.execute(
                "SELECT id FROM audio_files WHERE id = ?", (saved_audio,)
            ).fetchone()
        assert af is None

    def test_delete_nonexistent_returns_404(self, auth_client):
        resp = auth_client.delete("/api/audio/99999")
        assert resp.status_code == 404

    def test_delete_requires_authentication(self, client, auth_client, saved_audio):
        from fastapi.testclient import TestClient
        from app.main import app
        fresh = TestClient(app, raise_server_exceptions=True)
        resp = fresh.delete(f"/api/audio/{saved_audio}")
        assert resp.status_code == 401


class TestAccountPage:
    def test_shows_user_recordings(self, auth_client, saved_audio):
        resp = auth_client.get("/account")
        assert resp.status_code == 200
        # The page should contain the audio id somewhere
        assert str(saved_audio).encode() in resp.content

    def test_shows_empty_state_with_no_recordings(self, auth_client):
        resp = auth_client.get("/account")
        assert resp.status_code == 200
        # With no recordings, shows the empty state link or list is empty
        assert b"Generate" in resp.content or b"generate" in resp.content.lower()
