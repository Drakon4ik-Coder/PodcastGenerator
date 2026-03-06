import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "podcast.db")


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS audio_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                filename TEXT NOT NULL,
                original_text TEXT NOT NULL,
                segments_json TEXT NOT NULL,
                duration_seconds REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            PRAGMA journal_mode=WAL;
        """)
        # Migrations: add columns introduced after initial schema
        for stmt in [
            "ALTER TABLE audio_files ADD COLUMN title TEXT",
            "ALTER TABLE audio_files ADD COLUMN segment_durations_json TEXT",
        ]:
            try:
                conn.execute(stmt)
            except Exception:
                pass  # Column already exists


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
