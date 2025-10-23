from __future__ import annotations

import pytest
from unittest.mock import patch


@pytest.mark.usefixtures("clear_firebase_apps")
@patch("app.auth.get_current_user", return_value=None)
def test_app_boots_and_serves_root(mock_get_current_user, client) -> None:
    """Ensure the Flask app boots and serves the root route."""
    response = client.get("/")
    assert response.status_code in (200, 302)


@patch("app.auth.get_current_user", return_value=None)
def test_health_check_healthy(mock_get_current_user, client):
    """Test the /health endpoint when all services are healthy."""
    with patch("app.services.health.check_all_services") as mock_check:
        mock_check.return_value = (True, {"firestore": "OK", "gcs": "OK"})
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json == {"status": "ok", "details": {"firestore": "OK", "gcs": "OK"}}


@patch("app.auth.get_current_user", return_value=None)
def test_health_check_unhealthy(mock_get_current_user, client, monkeypatch):
    """Test the /health endpoint when a service is unhealthy."""
    monkeypatch.setattr(
        "app.services.health.check_all_services",
        lambda: (False, {"firestore": "OK", "gcs": "Error"}),
    )
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json == {"status": "error", "details": {"firestore": "OK", "gcs": "Error"}}


@pytest.fixture
def clear_firebase_apps():
    """Ensure firebase_admin global state does not leak across tests."""
    import firebase_admin

    for app_name in list(firebase_admin._apps.keys()):
        firebase_admin.delete_app(firebase_admin.get_app(name=app_name))
    yield
    for app_name in list(firebase_admin._apps.keys()):
        firebase_admin.delete_app(firebase_admin.get_app(name=app_name))
