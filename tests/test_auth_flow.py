from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from flask import g

from app.auth import COOKIE_NAME
from app.main import create_app


@patch("google.cloud.firestore.Client")
@pytest.fixture
def client(mock_firestore_client):
    app = create_app()
    app.config["TESTING"] = True
    app.config["AUTH_ENABLED"] = True
    with app.test_client() as client:
        with app.app_context():
            g.user = None  # Ensure g.user is clean for each test
        yield client


@patch("app.auth.ensure_user")
def test_auth_required_redirects_to_login(mock_ensure_user, client):
    def ensure_user_side_effect():
        g.user = None
        return None

    mock_ensure_user.side_effect = ensure_user_side_effect
    response = client.get("/profile")
    assert response.status_code == 302
    assert "/auth/login" in response.location


@patch("app.auth.ensure_user")
def test_auth_required_allows_logged_in_user(mock_ensure_user, client):
    def ensure_user_side_effect():
        g.user = {"uid": "test_user", "email": "test@example.com", "role": "member"}
        return g.user

    mock_ensure_user.side_effect = ensure_user_side_effect
    with patch("app.services.users.get_user") as mock_get_user:
        mock_get_user.return_value = MagicMock(id="test_user")
        response = client.get("/profile")
        assert response.status_code == 200


@patch("app.auth.ensure_user")
def test_admin_route_forbids_non_admin(mock_ensure_user, client):
    def ensure_user_side_effect():
        g.user = {"uid": "test_user", "email": "test@example.com", "role": "member"}
        return g.user

    mock_ensure_user.side_effect = ensure_user_side_effect
    response = client.get("/admin/")
    assert response.status_code == 403


@patch("app.auth.ensure_user")
def test_admin_route_allows_admin(mock_ensure_user, client):
    def ensure_user_side_effect():
        g.user = {"uid": "admin_user", "email": "admin@example.com", "role": "admin"}
        return g.user

    mock_ensure_user.side_effect = ensure_user_side_effect
    response = client.get("/admin/")
    assert response.status_code == 200


@patch("app.routes.auth.users_service.get_or_create_user")
@patch("app.routes.auth.firebase_auth.create_session_cookie")
@patch("app.routes.auth.firebase_auth.verify_id_token")
@patch("app.routes.auth.users_service.db")
def test_session_login_sets_cookie_attributes(
    mock_db,
    mock_verify_id_token,
    mock_create_session_cookie,
    mock_get_or_create_user,
    client,
):
    client.application.config.update(
        {
            "SESSION_COOKIE_SECURE": True,
            "SESSION_COOKIE_SAMESITE": "None",
        }
    )

    mock_db.transaction.return_value = MagicMock()
    decoded_token = {"uid": "user123", "email": "user@example.com", "name": "User"}
    mock_verify_id_token.return_value = decoded_token
    mock_create_session_cookie.return_value = "session-token"

    fake_user = SimpleNamespace(id="user123", email="user@example.com", role="member")
    fake_user.to_dict = lambda: {
        "id": fake_user.id,
        "email": fake_user.email,
        "role": fake_user.role,
    }
    mock_get_or_create_user.return_value = (fake_user, False)

    response = client.post(
        "/auth/sessionLogin",
        json={"idToken": "token-value", "rememberMe": False},
    )

    assert response.status_code == 302
    location = response.headers.get("Location")
    assert location in {"/", "http://localhost/"}

    cookie_headers = response.headers.getlist("Set-Cookie")
    assert len(cookie_headers) == 1
    session_cookie_header = cookie_headers[0]

    assert f"{COOKIE_NAME}=" in session_cookie_header
    assert "Path=/" in session_cookie_header
    assert "Secure" in session_cookie_header
    assert "HttpOnly" in session_cookie_header
    assert "SameSite=None" in session_cookie_header
    assert "Domain=" not in session_cookie_header


@patch("app.routes.main.users_service.get_user")
@patch("app.routes.main.buckets_service.list_buckets")
@patch("app.routes.auth.users_service.get_or_create_user")
@patch("app.routes.auth.firebase_auth.create_session_cookie")
@patch("app.routes.auth.firebase_auth.verify_id_token")
@patch("app.auth.firebase_auth.verify_session_cookie")
@patch("app.routes.auth.users_service.db")
def test_session_login_e2e_flow(
    mock_db,
    mock_verify_session_cookie,
    mock_verify_id_token,
    mock_create_session_cookie,
    mock_get_or_create_user,
    mock_list_buckets,
    mock_get_user,
    client,
):
    mock_db.transaction.return_value = MagicMock()
    decoded_token = {"uid": "user123", "email": "user@example.com", "name": "User"}
    mock_verify_id_token.return_value = decoded_token
    mock_create_session_cookie.return_value = "session-token"
    mock_verify_session_cookie.return_value = decoded_token

    fake_user = SimpleNamespace(id="user123", email="user@example.com", role="member")
    fake_user.to_dict = lambda: {
        "id": fake_user.id,
        "email": fake_user.email,
        "role": fake_user.role,
    }
    mock_get_or_create_user.return_value = (fake_user, False)
    mock_get_user.return_value = fake_user
    mock_list_buckets.return_value = []

    login_page = client.get("/auth/login", query_string={"next": "/profile"})
    assert login_page.status_code == 200

    response = client.post(
        "/auth/sessionLogin",
        json={"idToken": "token-value", "rememberMe": False, "next": "/profile"},
    )

    assert response.status_code == 302
    location = response.headers.get("Location")
    assert location in {"/profile", "http://localhost/profile"}

    profile_response = client.get("/profile")
    assert profile_response.status_code == 200
    session_cookie = client.get_cookie(COOKIE_NAME)
    assert session_cookie is not None
