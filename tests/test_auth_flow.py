from app.main import create_app
import pytest
from unittest.mock import patch, MagicMock
from flask import g


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
