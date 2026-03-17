"""TTS worker process — consumes from Kafka tts-jobs topic and runs Kokoro TTS."""
import os
import sys
import json
import uuid
import asyncio
import logging
import numpy as np
import soundfile as sf
from dotenv import load_dotenv

load_dotenv()

from .database import init_db, get_db, cleanup_stale_jobs
from .tts import get_pipeline, audio_to_base64, SAMPLE_RATE, validate_voice
from .kafka import KAFKA_BOOTSTRAP, TTS_JOBS_TOPIC, TTS_EVENTS_TOPIC

AUDIO_DIR = os.getenv("AUDIO_DIR", "storage/audio")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [worker] %(message)s")
log = logging.getLogger(__name__)


async def produce_event(producer, event: dict):
    await producer.send_and_wait(
        TTS_EVENTS_TOPIC,
        json.dumps(event).encode("utf-8"),
    )


async def process_job(msg_value: dict, producer):
    audio_id = msg_value["audio_id"]
    user_id = msg_value["user_id"]
    text = msg_value["text"]
    voice = validate_voice(msg_value.get("voice", ""))

    log.info("Processing job audio_id=%d voice=%s", audio_id, voice)

    # Mark as generating
    with get_db() as conn:
        conn.execute(
            "UPDATE audio_files SET status = 'generating' WHERE id = ? AND user_id = ?",
            (audio_id, user_id),
        )

    try:
        pipeline = get_pipeline()
        all_audio = []
        segments = []
        segment_durations = []
        index = 0

        # Run the blocking TTS pipeline in a thread so the asyncio event loop
        # stays alive for Kafka heartbeats (prevents session timeout / rebalance loop).
        loop = asyncio.get_event_loop()
        raw_segments = await loop.run_in_executor(
            None,
            lambda: [(gs, audio) for gs, _, audio in pipeline(text, voice=voice, speed=1.0)
                     if audio is not None and len(audio) > 0],
        )

        for gs, audio in raw_segments:
            audio_np = audio.detach().cpu().numpy() if hasattr(audio, "detach") else audio
            b64 = audio_to_base64(audio_np)

            # Store segment in DB
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO audio_segments (audio_id, segment_index, text, audio_b64) VALUES (?, ?, ?, ?)",
                    (audio_id, index, gs, b64),
                )

            # Notify via Kafka (lightweight — no audio payload)
            await produce_event(producer, {
                "audio_id": audio_id,
                "type": "segment",
                "index": index,
            })

            all_audio.append(audio_np.copy())
            segments.append(gs)
            segment_durations.append(float(len(audio_np) / SAMPLE_RATE))
            index += 1

        if all_audio:
            full_audio = np.concatenate(all_audio)
            filename = f"{uuid.uuid4()}.wav"
            path = os.path.join(AUDIO_DIR, filename)
            os.makedirs(AUDIO_DIR, exist_ok=True)
            sf.write(path, full_audio, SAMPLE_RATE, subtype="PCM_16")
            duration = len(full_audio) / SAMPLE_RATE

            with get_db() as conn:
                conn.execute(
                    """UPDATE audio_files
                       SET filename = ?, segments_json = ?, segment_durations_json = ?,
                           duration_seconds = ?, status = 'completed'
                       WHERE id = ?""",
                    (filename, json.dumps(segments), json.dumps(segment_durations), duration, audio_id),
                )
                # Clean up temp segments
                conn.execute("DELETE FROM audio_segments WHERE audio_id = ?", (audio_id,))

            await produce_event(producer, {"audio_id": audio_id, "type": "done"})
            log.info("Completed job audio_id=%d segments=%d duration=%.1fs", audio_id, index, duration)
        else:
            with get_db() as conn:
                conn.execute("UPDATE audio_files SET status = 'failed' WHERE id = ?", (audio_id,))
            await produce_event(producer, {"audio_id": audio_id, "type": "error", "message": "No audio generated"})

    except Exception as exc:
        log.exception("Error processing job audio_id=%d", audio_id)
        with get_db() as conn:
            conn.execute("UPDATE audio_files SET status = 'failed' WHERE id = ?", (audio_id,))
        await produce_event(producer, {"audio_id": audio_id, "type": "error", "message": str(exc)})


async def requeue_pending(producer):
    """Re-produce pending jobs (from restart recovery) to tts-jobs."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, user_id, original_text, voice FROM audio_files WHERE status = 'pending'"
        ).fetchall()
    for row in rows:
        msg = {
            "audio_id": row["id"],
            "user_id": row["user_id"],
            "text": row["original_text"],
            "voice": row["voice"] or "",
        }
        await producer.send_and_wait(TTS_JOBS_TOPIC, json.dumps(msg).encode("utf-8"))
        log.info("Re-queued pending job audio_id=%d", row["id"])


async def main():
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

    init_db()
    cleanup_stale_jobs()
    os.makedirs(AUDIO_DIR, exist_ok=True)

    producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP)
    await producer.start()

    # Re-queue any pending jobs from restart
    await requeue_pending(producer)

    consumer = AIOKafkaConsumer(
        TTS_JOBS_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id="tts-worker",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    log.info("Worker started, consuming from %s", TTS_JOBS_TOPIC)

    try:
        async for msg in consumer:
            try:
                job_data = json.loads(msg.value.decode("utf-8"))
                await process_job(job_data, producer)
                await consumer.commit()
            except Exception:
                log.exception("Failed to process message")
    finally:
        await consumer.stop()
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(main())
