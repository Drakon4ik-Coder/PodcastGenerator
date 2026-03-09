"""Tests for the in-memory job tracker."""
import asyncio
import pytest
from app.jobs import (
    register_job, get_job, get_user_active_jobs,
    add_segment, finish_job, subscribe, unsubscribe,
    remove_job, clear_all,
)


@pytest.fixture(autouse=True)
def clean_jobs():
    clear_all()
    yield
    clear_all()


class TestJobTracker:
    def test_register_and_get_job(self):
        job = register_job(1, 100)
        assert job.audio_id == 1
        assert job.user_id == 100
        assert get_job(1) is job

    def test_get_job_returns_none_for_unknown(self):
        assert get_job(999) is None

    def test_get_user_active_jobs(self):
        register_job(1, 100)
        register_job(2, 100)
        register_job(3, 200)
        assert set(get_user_active_jobs(100)) == {1, 2}
        assert get_user_active_jobs(200) == [3]
        assert get_user_active_jobs(999) == []

    def test_get_user_active_jobs_excludes_done(self):
        register_job(1, 100)
        register_job(2, 100)
        finish_job(1)
        assert get_user_active_jobs(100) == [2]

    def test_add_segment_notifies_subscribers(self):
        register_job(1, 100)
        q = subscribe(1)
        add_segment(1, {"index": 0, "text": "Hello.", "audio": "abc"})

        # Check subscriber got the notification
        event = q.get_nowait()
        assert event["type"] == "segment"
        assert event["text"] == "Hello."

        # Check segment is buffered
        job = get_job(1)
        assert len(job.segments) == 1

    def test_finish_job_notifies_subscribers(self):
        register_job(1, 100)
        q = subscribe(1)
        finish_job(1)

        event = q.get_nowait()
        assert event["type"] == "done"

        job = get_job(1)
        assert job.done is True

    def test_finish_job_with_error(self):
        register_job(1, 100)
        q = subscribe(1)
        finish_job(1, error="Something went wrong")

        event = q.get_nowait()
        assert event["type"] == "error"
        assert event["message"] == "Something went wrong"

        job = get_job(1)
        assert job.done is True
        assert job.error == "Something went wrong"

    def test_subscribe_unsubscribe(self):
        register_job(1, 100)
        q = subscribe(1)
        job = get_job(1)
        assert q in job._subscribers

        unsubscribe(1, q)
        assert q not in job._subscribers

    def test_subscribe_returns_none_for_unknown(self):
        assert subscribe(999) is None

    def test_remove_job(self):
        register_job(1, 100)
        assert get_job(1) is not None
        remove_job(1)
        assert get_job(1) is None

    def test_remove_nonexistent_job_no_error(self):
        remove_job(999)  # Should not raise
