import pytest
from app.main import app as flask_app

@pytest.fixture
def client():
    flask_app.config.update({
        "TESTING": True,
        "AUTH_ENABLED": True,
        "SECRET_KEY": "test-secret-key",
        "WTF_CSRF_ENABLED": False, # Disable CSRF for testing forms
    })
    with flask_app.test_client() as client:
        yield client
