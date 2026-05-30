import pytest

@pytest.fixture
def auth_header(client):
    client.post(
        "/api/auth/register",
        json={"username": "convuser", "email": "conv@example.com", "password": "password123"},
    )
    response = client.post(
        "/api/auth/login",
        json={"username": "convuser", "password": "password123"},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_create_conversation(client, auth_header):
    response = client.post(
        "/api/conversations",
        json={"title": "Test Conversation"},
        headers=auth_header
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Test Conversation"
    assert "id" in data


def test_list_conversations(client, auth_header):
    client.post(
        "/api/conversations",
        json={"title": "Conv 1"},
        headers=auth_header
    )
    client.post(
        "/api/conversations",
        json={"title": "Conv 2"},
        headers=auth_header
    )
    
    response = client.get("/api/conversations", headers=auth_header)
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 2
