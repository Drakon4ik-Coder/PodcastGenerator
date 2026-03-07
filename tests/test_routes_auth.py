"""Integration tests for authentication routes: /register, /login, /logout."""
import pytest


class TestRegister:
    def test_register_success_redirects_to_login(self, client):
        resp = client.post(
            "/register",
            data={"username": "bob", "password": "securepass"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    def test_register_duplicate_username_redirects_with_error(self, client):
        client.post("/register", data={"username": "bob", "password": "pass123"})
        resp = client.post(
            "/register",
            data={"username": "bob", "password": "different"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=taken" in resp.headers["location"]

    def test_register_short_username_redirects_with_error(self, client):
        resp = client.post(
            "/register",
            data={"username": "ab", "password": "validpass"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=short" in resp.headers["location"]

    def test_register_short_password_redirects_with_error(self, client):
        resp = client.post(
            "/register",
            data={"username": "validuser", "password": "12345"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=short" in resp.headers["location"]

    def test_register_page_renders(self, client):
        resp = client.get("/register")
        assert resp.status_code == 200
        assert b"Register" in resp.content or b"register" in resp.content.lower()


class TestLogin:
    def test_login_success_sets_cookie_and_redirects(self, client, registered_user):
        resp = client.post(
            "/login",
            data=registered_user,
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/app" in resp.headers["location"]
        assert "token" in resp.cookies

    def test_login_wrong_password_redirects_with_error(self, client, registered_user):
        resp = client.post(
            "/login",
            data={"username": registered_user["username"], "password": "wrongpass"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=invalid" in resp.headers["location"]

    def test_login_nonexistent_user_redirects_with_error(self, client):
        resp = client.post(
            "/login",
            data={"username": "ghost", "password": "password123"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=invalid" in resp.headers["location"]

    def test_login_page_renders(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"Sign in" in resp.content or b"Login" in resp.content


class TestLogout:
    def test_logout_clears_cookie_and_redirects(self, auth_client):
        resp = auth_client.get("/logout", follow_redirects=False)
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]
        # Cookie should be cleared (set to empty or expired)
        assert auth_client.cookies.get("token") is None or auth_client.cookies.get("token") == ""


class TestProtectedPages:
    def test_app_page_requires_auth(self, client):
        resp = client.get("/app", follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert "login" in resp.headers["location"]

    def test_account_page_requires_auth(self, client):
        resp = client.get("/account", follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert "login" in resp.headers["location"]

    def test_app_page_accessible_when_authenticated(self, auth_client):
        resp = auth_client.get("/app")
        assert resp.status_code == 200

    def test_account_page_accessible_when_authenticated(self, auth_client):
        resp = auth_client.get("/account")
        assert resp.status_code == 200

    def test_root_redirects_to_app_when_authenticated(self, auth_client):
        resp = auth_client.get("/", follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert "/app" in resp.headers["location"]

    def test_root_redirects_to_login_when_unauthenticated(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert "login" in resp.headers["location"]
