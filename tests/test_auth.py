def test_register(client):
    response = client.post(
        "/api/auth/register",
        json={"username": "testuser", "email": "test@example.com", "password": "password123"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "testuser"
    assert data["email"] == "test@example.com"
    assert "id" in data


def test_login(client):
    # 先注册
    client.post(
        "/api/auth/register",
        json={"username": "loginuser", "email": "login@example.com", "password": "password123"},
    )
    
    # 再登录
    response = client.post(
        "/api/auth/login",
        json={"username": "loginuser", "password": "password123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["user"]["username"] == "loginuser"


def test_login_wrong_password(client):
    client.post(
        "/api/auth/register",
        json={"username": "wronguser", "email": "wrong@example.com", "password": "password123"},
    )
    
    response = client.post(
        "/api/auth/login",
        json={"username": "wronguser", "password": "wrongpassword"},
    )
    assert response.status_code == 401
