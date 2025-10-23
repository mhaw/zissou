from unittest.mock import MagicMock, patch

from flask import g

from app.routes import main as main_routes


@patch("app.auth.get_current_user")
def test_new_submission_creates_task_document(mock_get_current_user, client, monkeypatch):
    mock_get_current_user.return_value = {
        "uid": "user-123",
        "email": "user@example.com",
        "role": "member",
    }

    submit_mock = MagicMock(return_value="task-abc")

    monkeypatch.setattr(main_routes.tasks_service, "submit_task", submit_mock)
    monkeypatch.setattr(main_routes.buckets_service, "list_buckets", lambda: [])
    monkeypatch.setattr(
        main_routes, "_enforce_submission_rate_limit", lambda action: (True, 0)
    )

    with client.application.app_context():
        g.user = mock_get_current_user.return_value
        response = client.post(
            "/new",
            data={"url": "https://example.com/article", "voice": "captains-log"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert "/progress/task-abc" in response.headers["Location"]
    submit_mock.assert_called_once_with(
        "https://example.com/article",
        voice="captains-log",
        bucket_id=None,
        user=mock_get_current_user.return_value,
    )
