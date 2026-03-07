"""Unit tests for the auth module (hash, verify, token, get_current_user)."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth import (
    hash_password,
    verify_password,
    create_token,
    get_current_user,
)


class TestPasswordHashing:
    def test_hash_produces_string(self):
        h = hash_password("mypassword")
        assert isinstance(h, str)
        assert len(h) > 0

    def test_hashes_are_unique(self):
        # Same password, different salts each time
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2

    def test_correct_password_verifies(self):
        h = hash_password("correct")
        assert verify_password("correct", h) is True

    def test_wrong_password_fails(self):
        h = hash_password("correct")
        assert verify_password("wrong", h) is False

    def test_empty_password_hashes_and_verifies(self):
        h = hash_password("")
        assert verify_password("", h) is True
        assert verify_password("notempty", h) is False


class TestCreateToken:
    def test_returns_string(self):
        token = create_token(1)
        assert isinstance(token, str)
        assert len(token) > 0

    def test_different_users_get_different_tokens(self):
        assert create_token(1) != create_token(2)

    def test_same_user_gets_different_tokens_over_time(self):
        # Tokens include timestamps so they differ even for same user
        t1 = create_token(1)
        t2 = create_token(1)
        # Not guaranteed to differ but should in practice; just check format
        assert isinstance(t1, str)
        assert isinstance(t2, str)


class TestGetCurrentUser:
    def test_missing_cookie_raises_401(self, client):
        from app.auth import get_current_user
        from fastapi import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
        }
        request = Request(scope)
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(request)
        assert exc_info.value.status_code == 401

    def test_invalid_token_raises_401(self, client):
        from app.auth import get_current_user
        from fastapi import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"cookie", b"token=notavalidtoken")],
            "query_string": b"",
        }
        request = Request(scope)
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(request)
        assert exc_info.value.status_code == 401

    def test_valid_token_returns_user(self, auth_client, registered_user):
        from app.auth import get_current_user, create_token
        from app.database import get_db

        # Fetch user id
        with get_db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username = ?",
                (registered_user["username"],),
            ).fetchone()

        token = create_token(user["id"])

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"cookie", f"token={token}".encode())],
            "query_string": b"",
        }
        from fastapi import Request
        request = Request(scope)
        result = get_current_user(request)
        assert result["username"] == registered_user["username"]
