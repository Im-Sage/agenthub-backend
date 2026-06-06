import pytest
from sqlalchemy import select

from app.models.agent import Agent
from app.models.code_change import CodeChange
from app.models.code_review import CodeReview
from app.models.conversation import Conversation
from app.models.deployment import Deployment
from app.models.message import Message
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.task import Task
from app.models.user import User
from app.schemas.enums import SenderType, TaskStatus

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


def test_delete_conversation_removes_tasks_and_task_artifacts(client, db_session, auth_header):
    response = client.post(
        "/api/conversations",
        json={"title": "Delete cascade"},
        headers=auth_header,
    )
    assert response.status_code == 201
    conversation_id = response.json()["id"]

    user = db_session.scalar(select(User).where(User.username == "convuser"))
    agent = Agent(name="Test Agent", code="delete-test-agent", adapter_type="mock")
    repository = Repository(
        user_id=user.id,
        name="repo",
        repo_url="https://example.com/repo.git",
        local_path="/tmp/repo",
        default_branch="main",
    )
    db_session.add_all([agent, repository])
    db_session.commit()

    parent_task = Task(
        conversation_id=conversation_id,
        agent_id=agent.id,
        status=TaskStatus.SUCCESS,
        instruction="parent",
    )
    child_task = Task(
        conversation_id=conversation_id,
        parent_task=parent_task,
        agent_id=agent.id,
        status=TaskStatus.SUCCESS,
        instruction="child",
    )
    message = Message(
        conversation_id=conversation_id,
        sender_type=SenderType.USER,
        content="hello",
    )
    db_session.add_all([parent_task, child_task, message])
    db_session.commit()

    code_change = CodeChange(
        task_id=child_task.id,
        repository_id=repository.id,
        repo_url=repository.repo_url,
        branch_name="branch",
        changed_files="[]",
        diff_text="",
    )
    db_session.add(code_change)
    db_session.commit()

    code_review = CodeReview(
        task_id=child_task.id,
        code_change_id=code_change.id,
        repository_id=repository.id,
        risk_level="low",
        summary="review",
        findings_json="[]",
        recommendations_json="[]",
    )
    deployment = Deployment(
        task_id=child_task.id,
        code_change_id=code_change.id,
        provider="local",
    )
    pull_request = PullRequest(
        task_id=child_task.id,
        code_change_id=code_change.id,
        repository_id=repository.id,
        branch_name="branch",
        commit_hash="abc123",
        title="PR",
        pr_url="https://example.com/pr/1",
    )
    db_session.add_all([code_review, deployment, pull_request])
    db_session.commit()
    code_change_id = code_change.id
    code_review_id = code_review.id
    deployment_id = deployment.id
    pull_request_id = pull_request.id

    response = client.delete(f"/api/conversations/{conversation_id}", headers=auth_header)
    assert response.status_code == 204

    assert db_session.get(Conversation, conversation_id) is None
    assert db_session.scalars(select(Message).where(Message.conversation_id == conversation_id)).all() == []
    assert db_session.scalars(select(Task).where(Task.conversation_id == conversation_id)).all() == []
    assert db_session.get(CodeChange, code_change_id) is None
    assert db_session.get(CodeReview, code_review_id) is None
    assert db_session.get(Deployment, deployment_id) is None
    assert db_session.get(PullRequest, pull_request_id) is None
