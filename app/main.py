import os, json, uuid, asyncio, threading, logging
import numpy as np
import soundfile as sf
from fastapi import FastAPI, Request, Response, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
load_dotenv()
from .database import init_db, get_db, cleanup_stale_jobs
from .auth import hash_password, verify_password, create_token, get_current_user
from .tts import get_pipeline, audio_to_base64, SAMPLE_RATE, VOICE, VOICES, validate_voice
from .kafka import KAFKA_BOOTSTRAP, TTS_JOBS_TOPIC, TTS_EVENTS_TOPIC
from . import jobs

AUDIO_DIR = os.getenv("AUDIO_DIR", "storage/audio")
log = logging.getLogger(__name__)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

_kafka_producer = None
_kafka_consumer_task = None


async def get_kafka_producer():
    global _kafka_producer
    if _kafka_producer is None:
        from aiokafka import AIOKafkaProducer
        _kafka_producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP)
        await _kafka_producer.start()
    return _kafka_producer


async def kafka_events_consumer():
    """Background task that consumes tts-events and routes to in-memory job tracker."""
    from aiokafka import AIOKafkaConsumer
    consumer = AIOKafkaConsumer(
        TTS_EVENTS_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id="web-events",
        auto_offset_reset="latest",
    )
    await consumer.start()
    log.info("Web Kafka events consumer started")
    try:
        async for msg in consumer:
            try:
                event = json.loads(msg.value.decode("utf-8"))
                audio_id = event.get("audio_id")
                event_type = event.get("type")

                if event_type == "segment":
                    jobs.add_segment(audio_id, {
                        "index": event["index"],
                        "text": event["text"],
                        "audio": event["audio"],
                    })
                elif event_type == "done":
                    jobs.finish_job(audio_id)
                    # Schedule cleanup after delay
                    asyncio.get_event_loop().call_later(60, jobs.remove_job, audio_id)
                elif event_type == "error":
                    jobs.finish_job(audio_id, error=event.get("message", "Unknown error"))
                    asyncio.get_event_loop().call_later(60, jobs.remove_job, audio_id)
            except Exception:
                log.exception("Error processing Kafka event")
    finally:
        await consumer.stop()


@app.on_event("startup")
async def startup():
    global _kafka_consumer_task
    init_db()
    cleanup_stale_jobs()
    os.makedirs(AUDIO_DIR, exist_ok=True)
    threading.Thread(target=get_pipeline, daemon=True).start()
    # Start Kafka events consumer in background
    try:
        _kafka_consumer_task = asyncio.create_task(kafka_events_consumer())
    except Exception:
        log.warning("Could not start Kafka consumer (Kafka may not be available)")


@app.on_event("shutdown")
async def shutdown():
    global _kafka_producer, _kafka_consumer_task
    if _kafka_consumer_task:
        _kafka_consumer_task.cancel()
    if _kafka_producer:
        await _kafka_producer.stop()


def user_from_request(request: Request):
    try:
        return dict(get_current_user(request))
    except HTTPException:
        return None

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return RedirectResponse("/app" if user_from_request(request) else "/login")

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = None, registered: bool = False):
    return templates.TemplateResponse("login.html", {"request": request, "active_tab": "login", "error": error, "registered": registered})

@app.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not user or not verify_password(password, user["password_hash"]):
        return RedirectResponse("/login?error=invalid", status_code=303)
    token = create_token(user["id"])
    response = RedirectResponse("/app", status_code=303)
    response.set_cookie("token", token, httponly=True, max_age=60*60*24*7, samesite="lax")
    return response

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, error: str = None):
    return templates.TemplateResponse("login.html", {"request": request, "active_tab": "register", "error": error})

@app.post("/register")
def register(username: str = Form(...), password: str = Form(...)):
    if len(username) < 3 or len(password) < 6:
        return RedirectResponse("/register?error=short", status_code=303)
    with get_db() as conn:
        if conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            return RedirectResponse("/register?error=taken", status_code=303)
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, hash_password(password)))
    return RedirectResponse("/login?registered=1", status_code=303)

@app.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("token")
    return response

@app.get("/app", response_class=HTMLResponse)
def app_page(request: Request):
    user = user_from_request(request)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("app.html", {"request": request, "user": user})

@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request):
    user = user_from_request(request)
    if not user:
        return RedirectResponse("/login")
    with get_db() as conn:
        files = conn.execute("SELECT * FROM audio_files WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)).fetchall()
    return templates.TemplateResponse("account.html", {"request": request, "user": user, "files": [dict(f) for f in files]})


@app.get("/api/voices")
def list_voices():
    return JSONResponse(VOICES)


@app.get("/api/jobs/active")
def active_jobs(user=Depends(get_current_user)):
    return {"jobs": jobs.get_user_active_jobs(user["id"])}


@app.post("/api/tts/generate")
async def generate_tts(request: Request, user=Depends(get_current_user)):
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(400, "No text provided")
    voice = validate_voice(body.get("voice", ""))

    # Create DB record with status='generating'
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO audio_files (user_id, filename, original_text, segments_json, status, voice) VALUES (?, ?, ?, ?, ?, ?)",
            (user["id"], "", text, "[]", "generating", voice),
        )
        audio_id = cursor.lastrowid

    # Register in-memory job
    job = jobs.register_job(audio_id, user["id"])

    # Try to produce to Kafka
    kafka_available = True
    try:
        producer = await get_kafka_producer()
        await producer.send_and_wait(
            TTS_JOBS_TOPIC,
            json.dumps({
                "audio_id": audio_id,
                "user_id": user["id"],
                "text": text,
                "voice": voice,
            }).encode("utf-8"),
        )
    except Exception:
        # Kafka not available — fall back to inline generation
        kafka_available = False

    if kafka_available:
        return StreamingResponse(
            job_event_stream(job, user["id"]),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Fallback: inline generation (no Kafka)
    async def inline_stream():
        yield f"data: {json.dumps({'type': 'started', 'audio_id': audio_id})}\n\n"

        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        all_audio = []
        segments = []

        def tts_worker():
            try:
                pipeline = get_pipeline()
                for gs, _, audio in pipeline(text, voice=voice, speed=1.0):
                    if audio is not None and len(audio) > 0:
                        audio_np = audio.detach().cpu().numpy() if hasattr(audio, 'detach') else audio
                        b64 = audio_to_base64(audio_np)
                        loop.call_soon_threadsafe(queue.put_nowait, ("segment", gs, audio_np.copy(), b64))
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc), None, None))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=tts_worker, daemon=True).start()
        index = 0
        error_occurred = False
        segment_durations: list = []
        while True:
            item = await queue.get()
            if item is None:
                break
            type_, gs, audio, b64 = item
            if type_ == "error":
                yield f"data: {json.dumps({'type': 'error', 'message': gs})}\n\n"
                error_occurred = True
                break
            all_audio.append(audio)
            segments.append(gs)
            segment_durations.append(float(len(audio) / SAMPLE_RATE))
            yield f"data: {json.dumps({'type': 'segment', 'index': index, 'text': gs, 'audio': b64})}\n\n"
            index += 1

        if not error_occurred and all_audio:
            full_audio = np.concatenate(all_audio)
            filename = f"{uuid.uuid4()}.wav"
            path = os.path.join(AUDIO_DIR, filename)
            sf.write(path, full_audio, SAMPLE_RATE, subtype="PCM_16")
            duration = len(full_audio) / SAMPLE_RATE
            with get_db() as conn:
                conn.execute(
                    """UPDATE audio_files
                       SET filename = ?, segments_json = ?, segment_durations_json = ?,
                           duration_seconds = ?, status = 'completed'
                       WHERE id = ?""",
                    (filename, json.dumps(segments), json.dumps(segment_durations), duration, audio_id))
            yield f"data: {json.dumps({'type': 'done', 'audio_id': audio_id})}\n\n"
        elif error_occurred:
            with get_db() as conn:
                conn.execute("UPDATE audio_files SET status = 'failed' WHERE id = ?", (audio_id,))

        jobs.finish_job(audio_id)
        jobs.remove_job(audio_id)

    return StreamingResponse(
        inline_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def job_event_stream(job, user_id: int):
    """SSE stream generator for Kafka-backed jobs."""
    yield f"data: {json.dumps({'type': 'started', 'audio_id': job.audio_id})}\n\n"

    # Catch-up: send any segments already buffered
    for seg in list(job.segments):
        yield f"data: {json.dumps({'type': 'segment', **seg})}\n\n"

    if job.done:
        if job.error:
            yield f"data: {json.dumps({'type': 'error', 'message': job.error})}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'done', 'audio_id': job.audio_id})}\n\n"
        return

    # Subscribe to live notifications
    queue = jobs.subscribe(job.audio_id)
    if not queue:
        return

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                # Keepalive
                yield ": keepalive\n\n"
    finally:
        jobs.unsubscribe(job.audio_id, queue)


@app.get("/api/tts/stream/{audio_id}")
async def stream_tts(audio_id: int, user=Depends(get_current_user)):
    """Reconnection endpoint for in-progress or completed jobs."""
    # Verify ownership
    with get_db() as conn:
        af = conn.execute(
            "SELECT * FROM audio_files WHERE id = ? AND user_id = ?",
            (audio_id, user["id"]),
        ).fetchone()
    if not af:
        raise HTTPException(404)

    status = af["status"] or "completed"

    if status == "completed":
        # Return segments from DB and done event
        async def completed_stream():
            segments = json.loads(af["segments_json"]) if af["segments_json"] else []
            yield f"data: {json.dumps({'type': 'started', 'audio_id': audio_id})}\n\n"
            for i, seg_text in enumerate(segments):
                yield f"data: {json.dumps({'type': 'segment', 'index': i, 'text': seg_text})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'audio_id': audio_id})}\n\n"
        return StreamingResponse(
            completed_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if status == "failed":
        async def failed_stream():
            yield f"data: {json.dumps({'type': 'started', 'audio_id': audio_id})}\n\n"
            yield f"data: {json.dumps({'type': 'error', 'message': 'Generation failed'})}\n\n"
        return StreamingResponse(
            failed_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Active or pending — check in-memory job
    job = jobs.get_job(audio_id)
    if not job:
        # Web restarted — re-register
        job = jobs.register_job(audio_id, user["id"])
        # Load any existing segments from DB
        with get_db() as conn:
            existing = conn.execute(
                "SELECT segment_index, text, audio_b64 FROM audio_segments WHERE audio_id = ? ORDER BY segment_index",
                (audio_id,),
            ).fetchall()
        for row in existing:
            jobs.add_segment(audio_id, {
                "index": row["segment_index"],
                "text": row["text"],
                "audio": row["audio_b64"],
            })

    return StreamingResponse(
        job_event_stream(job, user["id"]),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/audio/{audio_id}")
def serve_audio(audio_id: int, user=Depends(get_current_user)):
    with get_db() as conn:
        af = conn.execute("SELECT * FROM audio_files WHERE id = ? AND user_id = ?", (audio_id, user["id"])).fetchone()
    if not af:
        raise HTTPException(404)
    path = os.path.join(AUDIO_DIR, af["filename"])
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")

@app.patch("/api/audio/{audio_id}/title")
async def rename_audio(audio_id: int, request: Request, user=Depends(get_current_user)):
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "Title must not be empty")
    with get_db() as conn:
        af = conn.execute("SELECT id FROM audio_files WHERE id = ? AND user_id = ?", (audio_id, user["id"])).fetchone()
        if not af:
            raise HTTPException(404)
        conn.execute("UPDATE audio_files SET title = ? WHERE id = ?", (title, audio_id))
    return {"ok": True}

@app.delete("/api/audio/{audio_id}")
def delete_audio(audio_id: int, user=Depends(get_current_user)):
    with get_db() as conn:
        af = conn.execute("SELECT * FROM audio_files WHERE id = ? AND user_id = ?", (audio_id, user["id"])).fetchone()
        if not af:
            raise HTTPException(404)
        path = os.path.join(AUDIO_DIR, af["filename"])
        conn.execute("DELETE FROM audio_files WHERE id = ?", (audio_id,))
    if os.path.exists(path):
        os.remove(path)
    return {"ok": True}
