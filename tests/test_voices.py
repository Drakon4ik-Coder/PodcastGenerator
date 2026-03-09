"""Tests for voice selection feature."""
import json
import pytest
from unittest.mock import patch
from tests.conftest import parse_sse, generate_audio, make_mock_pipeline
from app.tts import VOICES, VOICE_IDS, VOICE, validate_voice


class TestVoicesEndpoint:
    def test_voices_endpoint_returns_list(self, client):
        resp = client.get("/api/voices")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_voice_entries_have_required_fields(self, client):
        resp = client.get("/api/voices")
        data = resp.json()
        for voice in data:
            assert "id" in voice
            assert "name" in voice
            assert "gender" in voice
            assert "accent" in voice

    def test_default_voice_in_list(self, client):
        resp = client.get("/api/voices")
        data = resp.json()
        ids = {v["id"] for v in data}
        assert "af_heart" in ids


class TestValidateVoice:
    def test_validate_voice_valid(self):
        assert validate_voice("af_heart") == "af_heart"
        assert validate_voice("am_adam") == "am_adam"
        assert validate_voice("bf_emma") == "bf_emma"

    def test_validate_voice_invalid_returns_default(self):
        assert validate_voice("nonexistent_voice") == VOICE
        assert validate_voice("") == VOICE
        assert validate_voice(None) == VOICE


class TestGenerateWithVoice:
    def test_generate_with_voice_parameter(self, auth_client, mock_pipeline):
        resp = auth_client.post(
            "/api/tts/generate",
            json={"text": "Hello world.", "voice": "am_adam"},
        )
        assert resp.status_code == 200
        events = parse_sse(resp.text)
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1

    def test_voice_stored_in_db(self, auth_client, mock_pipeline):
        done = generate_audio(auth_client, text="Test voice.", voice="bf_emma")
        from app.database import get_db
        with get_db() as conn:
            af = conn.execute(
                "SELECT voice FROM audio_files WHERE id = ?",
                (done["audio_id"],),
            ).fetchone()
        assert af["voice"] == "bf_emma"

    def test_generate_without_voice_uses_default(self, auth_client, mock_pipeline):
        done = generate_audio(auth_client, text="No voice specified.")
        from app.database import get_db
        with get_db() as conn:
            af = conn.execute(
                "SELECT voice FROM audio_files WHERE id = ?",
                (done["audio_id"],),
            ).fetchone()
        assert af["voice"] == VOICE

    def test_generate_with_invalid_voice_falls_back(self, auth_client, mock_pipeline):
        done = generate_audio(auth_client, text="Bad voice.", voice="invalid_voice")
        from app.database import get_db
        with get_db() as conn:
            af = conn.execute(
                "SELECT voice FROM audio_files WHERE id = ?",
                (done["audio_id"],),
            ).fetchone()
        assert af["voice"] == VOICE
