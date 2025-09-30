from unittest.mock import patch, MagicMock
from flask import g


@patch("app.auth.ensure_user")
def test_admin_page_unauthorized_access(mock_ensure_user, client):
    """Tests that a non-admin user gets a 403 when accessing /admin."""
    with client.application.app_context():
        mock_ensure_user.return_value = {
            "uid": "user_uid",
            "email": "user@example.com",
            "role": "member",
        }
        g.user = (
            mock_ensure_user.return_value
        )  # Ensure g.user is set for context processors
        response = client.get("/admin/")
        assert response.status_code == 403


@patch("app.auth.ensure_user")
def test_admin_page_authorized_access(mock_ensure_user, client):
    """Tests that an admin user can access /admin."""
    with client.application.app_context():
        mock_ensure_user.return_value = {
            "uid": "admin_uid",
            "email": "admin@example.com",
            "role": "admin",
        }
        g.user = (
            mock_ensure_user.return_value
        )  # Ensure g.user is set for context processors
        with patch("app.services.tasks.list_tasks") as mock_list_tasks, patch(
            "app.services.tasks.get_status_counts"
        ) as mock_get_status_counts, patch(
            "app.services.tasks.get_recent_activity"
        ) as mock_get_recent_activity, patch(
            "app.services.users.get_user_count"
        ) as mock_get_user_count, patch(
            "app.services.users.get_recent_user_count"
        ) as mock_get_recent_user_count, patch(
            "app.services.items.get_item_count"
        ) as mock_get_item_count, patch(
            "app.services.buckets.get_bucket_count"
        ) as mock_get_bucket_count, patch(
            "app.services.health.check_firestore_health"
        ) as mock_check_firestore_health, patch(
            "app.services.health.check_gcs_health"
        ) as mock_check_gcs_health:

            mock_list_tasks.return_value = ([], None)
            mock_get_status_counts.return_value = {}
            mock_get_recent_activity.return_value = {"counts": {}}
            mock_get_user_count.return_value = 0
            mock_get_recent_user_count.return_value = 0
            mock_get_item_count.return_value = 0
            mock_get_bucket_count.return_value = 0
            mock_check_firestore_health.return_value = (True, "OK")
            mock_check_gcs_health.return_value = (True, "OK")

            response = client.get("/admin/")
            assert response.status_code == 200
            assert b"Admin Dashboard" in response.data


@patch("app.auth.ensure_user")
def test_admin_page_filters(mock_ensure_user, client):
    """Tests the server-side filtering and sorting of the admin task list."""
    with client.application.app_context():
        mock_ensure_user.return_value = {
            "uid": "admin_uid",
            "email": "admin@example.com",
            "role": "admin",
        }
        g.user = (
            mock_ensure_user.return_value
        )  # Ensure g.user is set for context processors
        with patch("app.services.tasks.list_tasks") as mock_list_tasks, patch(
            "app.services.tasks.get_status_counts"
        ), patch("app.services.tasks.get_recent_activity"), patch(
            "app.services.users.get_user_count"
        ), patch(
            "app.services.users.get_recent_user_count"
        ), patch(
            "app.services.items.get_item_count"
        ), patch(
            "app.services.buckets.get_bucket_count"
        ), patch(
            "app.services.health.check_firestore_health"
        ) as mock_check_firestore_health, patch(
            "app.services.health.check_gcs_health"
        ) as mock_check_gcs_health:

            mock_list_tasks.return_value = ([], None)
            mock_check_firestore_health.return_value = (True, "OK")
            mock_check_gcs_health.return_value = (True, "OK")

            # Test with query parameters
            response = client.get("/admin/?q=test&sort=-updatedAt&status=COMPLETED")
            assert response.status_code == 200

            mock_list_tasks.assert_called_once_with(
                sort="-updatedAt",
                after=None,
                limit=50,
                status="COMPLETED",
                search_query="test",
            )


@patch("app.services.audit.log_admin_action")
@patch("app.auth.ensure_user")
def test_admin_actions_audit_logging(mock_ensure_user, mock_log_admin_action, client):
    """Tests that admin actions are audited."""
    with client.application.app_context():
        mock_ensure_user.return_value = {
            "uid": "admin_uid",
            "email": "admin@example.com",
            "role": "admin",
        }
        g.user = (
            mock_ensure_user.return_value
        )  # Ensure g.user is set for context processors
        with patch("app.services.audit.db") as mock_audit_db:  # Patch audit.db
            mock_audit_db.collection.return_value.document.return_value.set.return_value = (
                None  # Mock Firestore operations
            )
            # Test delete_item
            with patch("app.services.items.get_item") as mock_get_item, patch(
                "app.services.items.delete_item"
            ), patch("app.services.tasks.detach_item_from_tasks"), patch(
                "app.services.storage.extract_blob_name"
            ), patch(
                "app.services.storage.delete_blob"
            ):

                mock_get_item.return_value = MagicMock(id="item123")
                client.post("/admin/items/item123/delete")
                mock_log_admin_action.assert_any_call(
                    "delete_item", target_id="item123"
                )

            # Test retry_processing
            with patch("app.services.tasks.get_task") as mock_get_task, patch(
                "app.services.tasks.retry_task"
            ):

                mock_get_task.return_value = MagicMock(
                    id="task123", sourceUrl="http://example.com"
                )
                client.post("/admin/retry/task123")
                mock_log_admin_action.assert_any_call("retry_task", target_id="task123")

            # Test bulk_import
            with patch("app.services.tasks.create_task"):
                client.post(
                    "/admin/bulk_import", data={"urls_text": "http://example.com"}
                )
                mock_log_admin_action.assert_any_call(
                    "bulk_import",
                    details={"queued": 1, "failed": 0, "urls": ["http://example.com"]},
                )
