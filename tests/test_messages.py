import pytest

@pytest.fixture
def auth_header(client):
    client.post(
        "/api/auth/register",
        json={"username": "msguser", "email": "msg@example.com", "password": "password123"},
    )
    response = client.post(
        "/api/auth/login",
        json={"username": "msguser", "password": "password123"},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def conversation_id(client, auth_header):
    response = client.post(
        "/api/conversations",
        json={"title": "Msg Test"},
        headers=auth_header
    )
    return response.json()["id"]


def test_send_message(client, auth_header, conversation_id):
    response = client.post(
        "/api/messages",
        json={"conversation_id": conversation_id, "content": "Hello", "message_type": "text"},
        headers=auth_header
    )
    assert response.status_code == 201
    data = response.json()
    assert data["content"] == "Hello"


def test_send_mock_message(client, auth_header, conversation_id):
    # 发送 @mock 消息会创建任务
    response = client.post(
        "/api/messages",
        json={"conversation_id": conversation_id, "content": "@mock do something", "message_type": "text"},
        headers=auth_header
    )
    assert response.status_code == 201
    
    # 检查任务是否创建
    response = client.get(f"/api/tasks?conversation_id={conversation_id}", headers=auth_header)
    assert response.status_code == 200
    tasks = response.json()
    assert len(tasks) > 0
    assert tasks[0]["instruction"] == "do something"


def test_send_orchestrator_message(client, auth_header, conversation_id):
    # 发送 @orchestrator 消息会创建父任务
    response = client.post(
        "/api/messages",
        json={"conversation_id": conversation_id, "content": "@orchestrator build app", "message_type": "text"},
        headers=auth_header
    )
    assert response.status_code == 201
    
    # 检查任务是否创建
    response = client.get(f"/api/tasks?conversation_id={conversation_id}", headers=auth_header)
    assert response.status_code == 200
    tasks = response.json()
    # 应该至少有 1 个父任务（子任务由 Celery 异步动态生成，这里暂不等待）
    assert len(tasks) >= 1
    
    parent_tasks = [t for t in tasks if t["parent_task_id"] is None]
    assert len(parent_tasks) > 0
    assert parent_tasks[0]["instruction"] == "build app"
    assert '"requires_plan_confirmation": true' in parent_tasks[0]["metadata_json"]
    assert '"plan_status": "planning"' in parent_tasks[0]["metadata_json"]
