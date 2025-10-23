from types import SimpleNamespace
from unittest.mock import patch

from flask import g
from flask_wtf.csrf import generate_csrf

from app.routes import main as main_routes


@patch("app.auth.get_current_user")
def test_update_item_buckets_accepts_csrf(mock_get_current_user, client, monkeypatch):
    mock_get_current_user.return_value = {
        "uid": "user-123",
        "email": "user@example.com",
        "role": "member",
    }

    def fake_sync(item_id, bucket_ids):
        item = SimpleNamespace(buckets=bucket_ids)
        lookup = {
            bucket_id: SimpleNamespace(id=bucket_id, name=f"Bucket {bucket_id}")
            for bucket_id in bucket_ids
        }
        return item, lookup

    monkeypatch.setattr(main_routes, "_sync_item_buckets", fake_sync)
    monkeypatch.setattr(
        main_routes, "render_template", lambda *args, **kwargs: "<div>summary</div>"
    )

    with client.application.app_context():
        g.user = mock_get_current_user.return_value
        with client.application.test_request_context():
            token = generate_csrf()
        with client.session_transaction() as session:
            session["csrf_token"] = token

    response = client.post(
        "/api/items/item-1/buckets",
        json={"bucket_ids": ["bucket-1"]},
        headers={"X-CSRFToken": token},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["bucket_ids"] == ["bucket-1"]
