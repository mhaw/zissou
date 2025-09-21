import pytest
from unittest.mock import patch, MagicMock

# Since I don't know how the app is created, I'll try to import it directly
# and create a client. This might need to be adjusted.
from app.main import app as flask_app
from app.models.item import Item

def test_login_page_loads(client):
    """Tests that the login page loads correctly."""
    response = client.get("/auth/login")
    assert response.status_code == 200
    assert b"Sign in with Google" in response.data

@patch('app.services.items.get_item')
@patch('app.services.items.toggle_read_status')
@patch('app.auth._verify_session_cookie')
def test_toggle_read_status(mock_verify_session_cookie, mock_toggle_read_status, mock_get_item, client):
    mock_verify_session_cookie.return_value = {'uid': 'test_user_id'}
    
    # Mock the item returned by get_item
    mock_item = Item(id='test_item_id', title='Test Article', sourceUrl='http://example.com/article', is_read=False)
    mock_get_item.return_value = mock_item

    # Mock the return value of toggle_read_status
    mock_toggle_read_status.return_value = None # The function doesn't return anything specific

    response = client.post(f'/items/{mock_item.id}/read')

    mock_get_item.assert_called_once_with(mock_item.id)
    mock_toggle_read_status.assert_called_once_with(mock_item.id, 'test_user_id')
    assert response.status_code == 302 # Redirect
    assert response.headers['Location'] == '/' # Redirects to index by default
