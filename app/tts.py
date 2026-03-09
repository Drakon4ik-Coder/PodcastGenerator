import os
import io
import base64
import threading
import numpy as np
import soundfile as sf

SAMPLE_RATE = 24000
VOICE = os.getenv("TTS_VOICE", "af_heart")

VOICES = [
    {"id": "af_heart",    "name": "Heart",    "gender": "F", "accent": "American"},
    {"id": "af_bella",    "name": "Bella",    "gender": "F", "accent": "American"},
    {"id": "af_nicole",   "name": "Nicole",   "gender": "F", "accent": "American"},
    {"id": "af_sarah",    "name": "Sarah",    "gender": "F", "accent": "American"},
    {"id": "af_sky",      "name": "Sky",      "gender": "F", "accent": "American"},
    {"id": "am_adam",     "name": "Adam",     "gender": "M", "accent": "American"},
    {"id": "am_michael",  "name": "Michael",  "gender": "M", "accent": "American"},
    {"id": "bf_emma",     "name": "Emma",     "gender": "F", "accent": "British"},
    {"id": "bf_isabella", "name": "Isabella", "gender": "F", "accent": "British"},
    {"id": "bm_george",   "name": "George",   "gender": "M", "accent": "British"},
    {"id": "bm_lewis",    "name": "Lewis",    "gender": "M", "accent": "British"},
]
VOICE_IDS = {v["id"] for v in VOICES}


def validate_voice(voice_id: str) -> str:
    """Return voice_id if valid, else fall back to default VOICE."""
    if voice_id and voice_id in VOICE_IDS:
        return voice_id
    return VOICE

_pipeline = None
_pipeline_lock = threading.Lock()


def get_pipeline():
    global _pipeline
    if _pipeline is None:
        with _pipeline_lock:
            if _pipeline is None:
                from kokoro import KPipeline
                _pipeline = KPipeline(lang_code="a")
    return _pipeline


def audio_to_base64(audio) -> str:
    if hasattr(audio, 'detach'):
        audio = audio.detach().cpu().numpy()
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()
