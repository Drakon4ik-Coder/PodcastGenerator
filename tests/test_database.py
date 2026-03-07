"""Unit tests for database initialisation, migrations, and connection management."""
import sqlite3
import pytest
from app.database import init_db, get_db


class TestInitDb:
    def test_creates_users_table(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.database.DB_PATH", str(tmp_path / "db.db"))
        init_db()
        with get_db() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert "users" in tables

    def test_creates_audio_files_table(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.database.DB_PATH", str(tmp_path / "db.db"))
        init_db()
        with get_db() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert "audio_files" in tables

    def test_migration_adds_title_column(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.database.DB_PATH", str(tmp_path / "db.db"))
        init_db()
        with get_db() as conn:
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(audio_files)")
            }
        assert "title" in cols

    def test_migration_adds_segment_durations_column(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.database.DB_PATH", str(tmp_path / "db.db"))
        init_db()
        with get_db() as conn:
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(audio_files)")
            }
        assert "segment_durations_json" in cols

    def test_idempotent_when_called_twice(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.database.DB_PATH", str(tmp_path / "db.db"))
        init_db()
        # Should not raise on second call
        init_db()

    def test_wal_journal_mode_enabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.database.DB_PATH", str(tmp_path / "db.db"))
        init_db()
        with get_db() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"


class TestGetDb:
    def test_returns_row_factory_connection(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.database.DB_PATH", str(tmp_path / "db.db"))
        init_db()
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("alice", "hash"),
            )
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = 'alice'"
            ).fetchone()
        # Row factory makes columns accessible by name
        assert row["username"] == "alice"

    def test_commits_on_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.database.DB_PATH", str(tmp_path / "db.db"))
        init_db()
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("bob", "hash"),
            )
        # Open a new connection to verify the commit was durable
        raw = sqlite3.connect(str(tmp_path / "db.db"))
        row = raw.execute("SELECT username FROM users WHERE username='bob'").fetchone()
        raw.close()
        assert row is not None

    def test_does_not_commit_on_exception(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.database.DB_PATH", str(tmp_path / "db.db"))
        init_db()
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    ("charlie", "hash"),
                )
                raise RuntimeError("abort!")
        except RuntimeError:
            pass
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username='charlie'"
            ).fetchone()
        assert row is None


class TestUserCRUD:
    def test_insert_and_fetch_user(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.database.DB_PATH", str(tmp_path / "db.db"))
        init_db()
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("dave", "hashed"),
            )
        with get_db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username = 'dave'"
            ).fetchone()
        assert user["username"] == "dave"
        assert user["password_hash"] == "hashed"

    def test_username_unique_constraint(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.database.DB_PATH", str(tmp_path / "db.db"))
        init_db()
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("unique", "h"),
            )
        with pytest.raises(Exception):
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    ("unique", "h2"),
                )
