from unittest.mock import patch

import pytest

from app.auth import auth_required, role_required
from app.main import create_app

IAP_EMAIL = "accounts.google.com:user@example.com"
IAP_ID = "accounts.google.com:1234567890"
ADMIN_EMAIL = "accounts.google.com:admin@example.com"


@pytest.fixture
def client():
    with patch("google.cloud.firestore.Client"):
        app = create_app()
        app.config.update(
            TESTING=True,
            AUTH_ENABLED=True,
            AUTH_BACKEND="iap",
            ADMIN_EMAILS=["admin@example.com"],
        )

        # Ensure Firestore-backed helpers are skipped during tests.
        from app import auth as auth_module

        auth_module.users_service.db = None

        if "protected" not in app.view_functions:

            @app.route("/protected")
            @auth_required
            def protected():
                return "ok"

        if "admin_only" not in app.view_functions:

            @app.route("/admin-only")
            @role_required("admin")
            def admin_only():
                return "secret"

        with app.test_client() as test_client:
            yield test_client


def _iap_headers(email_header: str, id_header: str | None = None) -> dict[str, str]:
    headers = {
        "X-Goog-Authenticated-User-Email": email_header,
    }
    if id_header:
        headers["X-Goog-Authenticated-User-Id"] = id_header
    return headers


def test_auth_required_blocks_when_headers_missing(client):
    response = client.get("/protected")
    assert response.status_code == 401
    assert b"Authentication required" in response.data


def test_auth_required_allows_iap_user(client):
    response = client.get("/protected", headers=_iap_headers(IAP_EMAIL, IAP_ID))
    assert response.status_code == 200
    assert response.data == b"ok"


def test_role_required_denies_non_admin(client):
    response = client.get("/admin-only", headers=_iap_headers(IAP_EMAIL, IAP_ID))
    assert response.status_code == 403


def test_role_required_allows_admin(client):
    response = client.get(
        "/admin-only",
        headers=_iap_headers(ADMIN_EMAIL, ADMIN_EMAIL),
    )
    assert response.status_code == 200
    assert response.data == b"secret"
