import os

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TTS_JOBS_TOPIC = "tts-jobs"
TTS_EVENTS_TOPIC = "tts-events"
