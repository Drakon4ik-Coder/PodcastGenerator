"""Integration tests for the TTS streaming endpoint."""
import base64
import os
import pytest
from unittest.mock import patch
from tests.conftest import parse_sse, generate_audio, make_mock_pipeline


class TestTTSGenerate:
    def test_requires_authentication(self, client):
        resp = client.post("/api/tts/generate", json={"text": "Hello."})
        assert resp.status_code == 401

    def test_rejects_empty_text(self, auth_client, mock_pipeline):
        resp = auth_client.post("/api/tts/generate", json={"text": ""})
        assert resp.status_code == 400

    def test_rejects_whitespace_only_text(self, auth_client, mock_pipeline):
        resp = auth_client.post("/api/tts/generate", json={"text": "   "})
        assert resp.status_code == 400

    def test_streams_sse_content_type(self, auth_client, mock_pipeline):
        resp = auth_client.post(
            "/api/tts/generate", json={"text": "Hello world."}
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

    def test_yields_segment_events(self, auth_client, mock_pipeline):
        resp = auth_client.post(
            "/api/tts/generate",
            json={"text": "First sentence. Second sentence."},
        )
        assert resp.status_code == 200
        events = parse_sse(resp.text)
        segments = [e for e in events if e["type"] == "segment"]
        assert len(segments) >= 1

    def test_segment_events_have_required_fields(self, auth_client, mock_pipeline):
        resp = auth_client.post(
            "/api/tts/generate",
            json={"text": "Hello world. Goodbye world."},
        )
        events = parse_sse(resp.text)
        segments = [e for e in events if e["type"] == "segment"]

        for i, seg in enumerate(segments):
            assert seg["index"] == i, "index must be sequential"
            assert isinstance(seg["text"], str) and seg["text"], "text must be non-empty"
            assert isinstance(seg["audio"], str), "audio must be a base64 string"

            # Verify audio is valid base64
            raw = base64.b64decode(seg["audio"])
            assert raw[:4] == b"RIFF", "audio must be a WAV file"

    def test_segment_indices_are_sequential(self, auth_client, mock_pipeline):
        resp = auth_client.post(
            "/api/tts/generate",
            json={"text": "One. Two. Three."},
        )
        events = parse_sse(resp.text)
        indices = [e["index"] for e in events if e["type"] == "segment"]
        assert indices == list(range(len(indices)))

    def test_done_event_present(self, auth_client, mock_pipeline):
        resp = auth_client.post(
            "/api/tts/generate", json={"text": "Hello world."}
        )
        events = parse_sse(resp.text)
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1

    def test_done_event_contains_audio_id(self, auth_client, mock_pipeline):
        resp = auth_client.post(
            "/api/tts/generate", json={"text": "Hello world."}
        )
        events = parse_sse(resp.text)
        done = next(e for e in events if e["type"] == "done")
        assert "audio_id" in done
        assert isinstance(done["audio_id"], int)

    def test_audio_file_saved_to_disk(self, auth_client, mock_pipeline, tmp_path, monkeypatch):
        audio_dir = str(tmp_path / "audio")
        monkeypatch.setattr("app.main.AUDIO_DIR", audio_dir)
        os.makedirs(audio_dir, exist_ok=True)

        resp = auth_client.post(
            "/api/tts/generate", json={"text": "Save me to disk."}
        )
        events = parse_sse(resp.text)
        done = next(e for e in events if e["type"] == "done")

        # The saved file should be fetchable
        audio_resp = auth_client.get(f"/api/audio/{done['audio_id']}")
        assert audio_resp.status_code == 200
        assert audio_resp.headers["content-type"] == "audio/wav"

    def test_segments_come_before_done(self, auth_client, mock_pipeline):
        resp = auth_client.post(
            "/api/tts/generate", json={"text": "Hello. World."}
        )
        events = parse_sse(resp.text)
        types = [e["type"] for e in events]
        assert "segment" in types
        assert "done" in types
        assert types.index("done") > types.index("segment"), \
            "done event must come after all segment events"

    def test_segment_durations_stored(self, auth_client, mock_pipeline):
        """Verify segment_durations_json is persisted for caption sync."""
        resp = auth_client.post(
            "/api/tts/generate", json={"text": "Hello. World."}
        )
        events = parse_sse(resp.text)
        done = next(e for e in events if e["type"] == "done")

        from app.database import get_db
        import json
        with get_db() as conn:
            af = conn.execute(
                "SELECT segment_durations_json FROM audio_files WHERE id = ?",
                (done["audio_id"],),
            ).fetchone()

        durations = json.loads(af["segment_durations_json"])
        assert isinstance(durations, list)
        assert len(durations) >= 1
        assert all(isinstance(d, float) and d > 0 for d in durations)
