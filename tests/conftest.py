import os
import pytest
from flask import g
from unittest.mock import patch

@pytest.fixture(scope="session")
def app():
    from app import create_app  # adjust import if your factory path differs
    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        LOGIN_DISABLED=False,  # we’ll control login manually
        SERVER_NAME="localhost",
        PREFERRED_URL_SCHEME="https",
    )
    # Simulate local dev mode (no IAP headers)
    app.config["AUTH_MODE"] = "local"
    os.environ["ENV"] = "development" # Set ENV to development

    with app.app_context():
        yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def mock_user():
    """Return a dummy user object similar to what Firebase/IAP injects."""
    return {
        "uid": "test_user_123",
        "email": "tester@example.com",
        "name": "Test User",
        "role": "admin",
    }


@pytest.fixture(autouse=True)
def mock_auth(monkeypatch, mock_user):
    """
    Automatically patch any authentication decorators or helper functions.
    Works for Firebase, IAP, or Flask-Login implementations.
    """

    # Patch function that loads current user
    def fake_get_current_user():
        g.user = mock_user
        return mock_user

    # If you have `auth.get_current_user()` or similar
    try:
        from app import auth
        monkeypatch.setattr(auth, "get_current_user", fake_get_current_user)
    except ImportError:
        pass

    # For Flask-Login pattern
    try:
        from flask_login import AnonymousUserMixin
        monkeypatch.setattr("flask_login.utils._get_user", lambda: mock_user)
    except Exception:
        pass

    yield


@pytest.fixture(autouse=True)
def disable_external_calls(monkeypatch):
    """Avoid hitting Firestore, Cloud Tasks, or GCS during tests."""
    # Mock Firestore client
    monkeypatch.setattr("google.cloud.firestore.Client", lambda: None)
    # Mock any network calls if needed
    yield


@pytest.fixture(autouse=True)
def force_https(monkeypatch):
    """Prevent redirect issues from Flask-Talisman or HTTPS redirect middlewares."""
    try:
        monkeypatch.setattr("flask.request.is_secure", True)
    except RuntimeError:
        pass  # Ignore if no request context
    yield