from contextlib import contextmanager
from unittest.mock import patch

from fastapi import Response

from BACKEND.routes.auth import LoginIn, login


def test_login_uses_http_only_cookie_without_returning_session_token():
    class FakeDatabase:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def execute(self, *_):
            return self

        def fetchone(self):
            return {
                "id": 1,
                "name": "Test User",
                "email": "user@example.com",
                "password_hash": "hash",
            }

    @contextmanager
    def fake_database_connection():
        yield FakeDatabase()

    response = Response()
    with (
        patch(
            "BACKEND.routes.auth.database_connection",
            fake_database_connection,
        ),
        patch("BACKEND.routes.auth.verify_password", return_value=True),
        patch("BACKEND.routes.auth.create_session", return_value="session-token"),
    ):
        result = login(
            LoginIn(email="user@example.com", password="password"),
            response,
        )

    assert "token" not in result
    set_cookie = response.headers["set-cookie"]
    assert "traffic_session=session-token" in set_cookie
    assert "httponly" in set_cookie.lower()
