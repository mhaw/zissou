import pytest
from unittest.mock import patch

# Patch firestore.Client before importing app.main.app
# This ensures that module-level initializations of firestore.Client are mocked
with patch("google.cloud.firestore.Client") as mock_firestore_client:
    from app.main import app as flask_app

    @pytest.fixture
    def client():
        flask_app.config.update(
            {
                "TESTING": True,
                "AUTH_ENABLED": True,
                "SECRET_KEY": "test-secret-key",
                "WTF_CSRF_ENABLED": False,  # Disable CSRF for testing forms
            }
        )
        with flask_app.test_client() as client:
            yield client
