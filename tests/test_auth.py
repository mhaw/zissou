from unittest.mock import patch

from flask import g

from app.auth import build_user_context
from app.models.item import Item


def test_login_page_loads(client):
    """Tests that the login page loads correctly."""
    client.application.config.update(AUTH_BACKEND="firebase")
    response = client.get("/auth/login")
    assert response.status_code == 200
    assert b"Sign in to Zissou" in response.data


@patch("app.services.items.get_item")
@patch("app.services.items.toggle_read_status")
@patch("app.auth.ensure_user")
def test_toggle_read_status(
    mock_ensure_user, mock_toggle_read_status, mock_get_item, client
):
    def ensure_user_side_effect():
        g.user = {"uid": "test_user_id"}
        return g.user

    mock_ensure_user.side_effect = ensure_user_side_effect

    # Mock the item returned by get_item
    mock_item = Item(
        id="test_item_id",
        title="Test Article",
        sourceUrl="http://example.com/article",
        is_read=False,
    )
    mock_get_item.return_value = mock_item

    # Mock the return value of toggle_read_status
    mock_toggle_read_status.return_value = (
        None  # The function doesn't return anything specific
    )

    response = client.post(f"/items/{mock_item.id}/read")

    mock_get_item.assert_called_once_with(mock_item.id)
    mock_toggle_read_status.assert_called_once_with(mock_item.id, "test_user_id")
    assert response.status_code == 302  # Redirect
    assert response.headers["Location"] == "/"  # Redirects to index by default


def test_build_user_context_admin_user(client):
    """Tests that a user with an email in ADMIN_EMAILS gets the admin role."""
    # The role is now determined by the db_user passed to build_user_context
    claims = {"uid": "admin_uid", "email": "admin@example.com", "name": "Admin User"}
    db_user_data = {"role": "admin"}
    user_context = build_user_context(claims, db_user_data)
    assert user_context["role"] == "admin"


def test_build_user_context_non_admin_user(client):
    """Tests that a user without an email in ADMIN_EMAILS gets the member role."""
    claims = {"uid": "user_uid", "email": "user@example.com", "name": "Regular User"}
    db_user_data = {"role": "member"}
    user_context = build_user_context(claims, db_user_data)
    assert user_context["role"] == "member"


def test_build_user_context_no_admin_emails(client):
    """Tests that if ADMIN_EMAILS is not set, no user gets the admin role.
    This test is now redundant as ADMIN_EMAILS no longer determines role.
    It now tests the default role if db_user_data is not provided or has no role.
    """
    claims = {"uid": "user_uid", "email": "user@example.com", "name": "Regular User"}
    # Simulate no role provided from DB, should default to 'member'
    user_context = build_user_context(claims, {})
    assert user_context["role"] == "member"
    # Test with explicit member role
    user_context = build_user_context(claims, {"role": "member"})
    assert user_context["role"] == "member"
