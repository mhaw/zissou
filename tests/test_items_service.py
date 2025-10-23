import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from app.services.items import get_random_unread_item, Item
from app.models.item import Item as ItemModel
from app.config import AppConfig


@pytest.fixture
def mock_firestore_client():
    """Fixture to mock the Firestore client."""
    with patch("app.services.items.db") as mock_db:
        yield mock_db


@pytest.fixture
def mock_item_data():
    """Fixture to provide mock item data."""
    return {
        "id": "item123",
        "title": "Test Item",
        "sourceUrl": "http://example.com/test",
        "userId": "user123",
        "is_read": False,
        "is_archived": False,
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    }


def test_get_random_unread_item_success(mock_firestore_client, mock_item_data):
    """
    Tests that get_random_unread_item returns a random unread item when available.
    """
    # Mock a document snapshot
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.id = mock_item_data["id"]
    mock_doc.to_dict.return_value = mock_item_data

    # Mock the query stream to return the mock document
    mock_firestore_client.collection.return_value.where.return_value.where.return_value.where.return_value.order_by.return_value.start_at.return_value.limit.return_value.stream.return_value = [mock_doc]
    mock_firestore_client.collection.return_value.document.return_value.id = "random_doc_id"

    item = get_random_unread_item("user123")

    assert item is not None
    assert item.id == mock_item_data["id"]
    assert item.title == mock_item_data["title"]
    assert not item.is_read


def test_get_random_unread_item_no_items(mock_firestore_client):
    """
    Tests that get_random_unread_item returns None when no unread items are found.
    """
    # Mock the query stream to return an empty list
    mock_firestore_client.collection.return_value.where.return_value.where.return_value.where.return_value.order_by.return_value.start_at.return_value.limit.return_value.stream.return_value = []
    mock_firestore_client.collection.return_value.where.return_value.where.return_value.where.return_value.order_by.return_value.limit.return_value.stream.return_value = []
    mock_firestore_client.collection.return_value.document.return_value.id = "random_doc_id"

    item = get_random_unread_item("user123")
    assert item is None


@patch("app.auth.get_current_user")
@patch("app.services.items.get_random_unread_item")
def test_surprise_me_route_redirects_to_item(
    mock_get_random_unread_item, mock_get_current_user, client, mock_item_data
):
    """
    Tests that the /surprise_me route redirects to a random item.
    """
    mock_get_current_user.return_value = {"uid": "user123"}
    mock_item = ItemModel.from_dict(mock_item_data)
    mock_get_random_unread_item.return_value = mock_item

    response = client.get("/surprise_me")

    mock_get_random_unread_item.assert_called_once_with("user123")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/items/{mock_item.id}"


@patch("app.auth.get_current_user")
@patch("app.services.items.get_random_unread_item")
def test_surprise_me_route_no_items_redirects_to_root(
    mock_get_random_unread_item, mock_get_current_user, client
):
    """
    Tests that the /surprise_me route redirects to the root if no unread items are found.
    """
    mock_get_current_user.return_value = {"uid": "user123"}
    mock_get_random_unread_item.return_value = None

    response = client.get("/surprise_me")

    mock_get_random_unread_item.assert_called_once_with("user123")
    assert response.status_code == 302
    assert response.headers["Location"] == "/"
