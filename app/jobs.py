import asyncio
from dataclasses import dataclass, field


@dataclass
class ActiveJob:
    audio_id: int
    user_id: int
    segments: list = field(default_factory=list)
    done: bool = False
    error: str | None = None
    _subscribers: list = field(default_factory=list)


_jobs: dict[int, ActiveJob] = {}


def register_job(audio_id: int, user_id: int) -> ActiveJob:
    job = ActiveJob(audio_id=audio_id, user_id=user_id)
    _jobs[audio_id] = job
    return job


def get_job(audio_id: int) -> ActiveJob | None:
    return _jobs.get(audio_id)


def get_user_active_jobs(user_id: int) -> list[int]:
    return [j.audio_id for j in _jobs.values() if j.user_id == user_id and not j.done]


def add_segment(audio_id: int, segment_data: dict):
    job = _jobs.get(audio_id)
    if not job:
        return
    job.segments.append(segment_data)
    for q in job._subscribers:
        q.put_nowait({"type": "segment", **segment_data})


def finish_job(audio_id: int, error: str | None = None):
    job = _jobs.get(audio_id)
    if not job:
        return
    job.done = True
    job.error = error
    event = {"type": "error", "message": error} if error else {"type": "done", "audio_id": audio_id}
    for q in job._subscribers:
        q.put_nowait(event)


def subscribe(audio_id: int) -> asyncio.Queue:
    job = _jobs.get(audio_id)
    if not job:
        return None
    q: asyncio.Queue = asyncio.Queue()
    job._subscribers.append(q)
    return q


def unsubscribe(audio_id: int, queue: asyncio.Queue):
    job = _jobs.get(audio_id)
    if job and queue in job._subscribers:
        job._subscribers.remove(queue)


def remove_job(audio_id: int):
    _jobs.pop(audio_id, None)


def clear_all():
    """Clear all jobs. Used in tests."""
    _jobs.clear()
