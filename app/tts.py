import os
import io
import base64
import threading
import numpy as np
import soundfile as sf

SAMPLE_RATE = 24000
VOICE = os.getenv("TTS_VOICE", "af_heart")

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
