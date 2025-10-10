from __future__ import annotations

import pytest


@pytest.mark.usefixtures("clear_firebase_apps")
def test_app_boots_and_serves_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the Flask app boots with configured cache/limiter and serves the root route."""
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("GCS_BUCKET", "test-bucket")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("AUTH_ENABLED", "false")

    from app import create_app

    app = create_app()
    client = app.test_client()

    response = client.get("/")
    assert response.status_code in (200, 302)


@pytest.fixture
def clear_firebase_apps():
    """Ensure firebase_admin global state does not leak across tests."""
    import firebase_admin

    for app_name in list(firebase_admin._apps.keys()):
        firebase_admin.delete_app(firebase_admin.get_app(name=app_name))
    yield
    for app_name in list(firebase_admin._apps.keys()):
        firebase_admin.delete_app(firebase_admin.get_app(name=app_name))
