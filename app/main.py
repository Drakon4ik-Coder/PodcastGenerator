import os, json, uuid, asyncio, threading
import numpy as np
import soundfile as sf
from fastapi import FastAPI, Request, Response, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
load_dotenv()
from .database import init_db, get_db
from .auth import hash_password, verify_password, create_token, get_current_user
from .tts import get_pipeline, audio_to_base64, SAMPLE_RATE, VOICE

AUDIO_DIR = os.getenv("AUDIO_DIR", "storage/audio")
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.on_event("startup")
def startup():
    init_db()
    os.makedirs(AUDIO_DIR, exist_ok=True)
    threading.Thread(target=get_pipeline, daemon=True).start()

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

@app.post("/api/tts/generate")
async def generate_tts(request: Request, user=Depends(get_current_user)):
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(400, "No text provided")

    async def event_stream():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        all_audio = []
        segments = []

        def tts_worker():
            try:
                pipeline = get_pipeline()
                for gs, _, audio in pipeline(text, voice=VOICE, speed=1.0):
                    if audio is not None and len(audio) > 0:
                        b64 = audio_to_base64(audio)
                        loop.call_soon_threadsafe(queue.put_nowait, ("segment", gs, audio.copy(), b64))
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc), None, None))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=tts_worker, daemon=True).start()
        index = 0
        error_occurred = False
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
            yield f"data: {json.dumps({'type': 'segment', 'index': index, 'text': gs, 'audio': b64})}\n\n"
            index += 1

        if not error_occurred and all_audio:
            full_audio = np.concatenate(all_audio)
            filename = f"{uuid.uuid4()}.wav"
            path = os.path.join(AUDIO_DIR, filename)
            sf.write(path, full_audio, SAMPLE_RATE, subtype="PCM_16")
            duration = len(full_audio) / SAMPLE_RATE
            with get_db() as conn:
                cursor = conn.execute(
                    "INSERT INTO audio_files (user_id, filename, original_text, segments_json, duration_seconds) VALUES (?, ?, ?, ?, ?)",
                    (user["id"], filename, text, json.dumps(segments), duration))
                audio_id = cursor.lastrowid
            yield f"data: {json.dumps({'type': 'done', 'audio_id': audio_id})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
