import asyncio
import json
import math
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.code_chunk import CodeChunk
from app.models.repository import Repository
from app.models.user import Base, User
from app.mcp.repository_resolver import ResolvedWorkspace
from app.rag.embeddings import (
    HashEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)
from app.rag.index_service import RepositoryIndexService
from app.services import repo_service
from app.workers import agent_tasks, index_tasks


class FakeResolver:
    def __init__(self, workspaces):
        self.workspaces = workspaces
        self.calls = []

    def resolve_owned_workspace(self, repository_id, user_id):
        self.calls.append((repository_id, user_id))
        return ResolvedWorkspace(
            repository_id=repository_id,
            user_id=user_id,
            local_path=str(self.workspaces[repository_id]),
        )


class RecordingEmbeddingProvider:
    def __init__(self, dimensions=8):
        self.dimensions = dimensions
        self.calls = []
        self.fail = False

    async def embed_documents(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("embedding unavailable")
        return [
            [float(index + 1)] + [0.0] * (self.dimensions - 1)
            for index, _ in enumerate(texts)
        ]

    async def embed_query(self, text):
        return (await self.embed_documents([text]))[0]


@pytest.fixture
def index_environment(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    with sessions() as db:
        first_user = User(
            username="first",
            email="first@example.com",
            password_hash="hash",
        )
        second_user = User(
            username="second",
            email="second@example.com",
            password_hash="hash",
        )
        db.add_all([first_user, second_user])
        db.flush()
        db.add_all(
            [
                Repository(
                    id=1,
                    user_id=first_user.id,
                    name="first",
                    repo_url="https://example.com/first.git",
                    local_path=str(first_workspace),
                    default_branch="main",
                ),
                Repository(
                    id=2,
                    user_id=second_user.id,
                    name="second",
                    repo_url="https://example.com/second.git",
                    local_path=str(second_workspace),
                    default_branch="main",
                ),
            ]
        )
        db.commit()
    provider = RecordingEmbeddingProvider()
    resolver = FakeResolver(
        {1: first_workspace, 2: second_workspace}
    )
    service = RepositoryIndexService(
        session_factory=sessions,
        repository_resolver=resolver,
        embedding_provider=provider,
        batch_size=2,
    )
    return sessions, service, provider, first_workspace, second_workspace


def chunks_for(sessions, repository_id):
    with sessions() as db:
        return list(
            db.scalars(
                select(CodeChunk)
                .where(CodeChunk.repository_id == repository_id)
                .order_by(CodeChunk.id)
            )
        )


def test_hash_embeddings_are_stable_fixed_size_and_normalized():
    provider = HashEmbeddingProvider(dimensions=32)

    first = asyncio.run(provider.embed_query("alpha beta alpha"))
    again = asyncio.run(provider.embed_query("alpha beta alpha"))
    changed = asyncio.run(provider.embed_query("alpha gamma"))

    assert first == again
    assert first != changed
    assert len(first) == 32
    assert math.isclose(
        math.sqrt(sum(value * value for value in first)),
        1.0,
    )


def test_openai_compatible_provider_sends_expected_request():
    async def handler(request):
        assert request.url.path == "/v1/embeddings"
        assert request.headers["authorization"] == "Bearer test-secret"
        assert json.loads(request.content) == {
            "model": "embedding-model",
            "input": ["one", "two"],
        }
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://embeddings.example/v1",
        api_key="test-secret",
        model="embedding-model",
        dimensions=2,
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(provider.embed_documents(["one", "two"]))

    assert result == [[1.0, 0.0], [0.0, 1.0]]


def test_openai_compatible_provider_rejects_dimension_mismatch():
    async def handler(request):
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0]}]},
        )

    provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://embeddings.example/v1",
        api_key="secret-that-must-not-leak",
        model="embedding-model",
        dimensions=2,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match="dimension"):
        asyncio.run(provider.embed_documents(["one"]))


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (401, "unauthorized"),
        (429, "rate limited"),
        (503, "service failed"),
    ],
)
def test_openai_compatible_provider_handles_retryable_http_errors(
    status_code,
    message,
):
    async def handler(request):
        return httpx.Response(status_code, text="secret response")

    provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://embeddings.example/v1",
        api_key="secret-that-must-not-leak",
        model="embedding-model",
        dimensions=2,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match=message) as error:
        asyncio.run(provider.embed_documents(["one"]))
    assert "secret-that-must-not-leak" not in str(error.value)


def test_openai_compatible_provider_rejects_count_mismatch():
    async def handler(request):
        return httpx.Response(200, json={"data": []})

    provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://embeddings.example/v1",
        api_key="secret",
        model="embedding-model",
        dimensions=2,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match="count"):
        asyncio.run(provider.embed_documents(["one"]))


def test_full_index_is_idempotent_and_replaces_changed_file(
    index_environment,
):
    sessions, service, provider, workspace, _ = index_environment
    source = workspace / "service.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")

    first = asyncio.run(service.index_repository(1))
    original = chunks_for(sessions, 1)
    second = asyncio.run(service.index_repository(1))
    repeated = chunks_for(sessions, 1)
    source.write_text("def value():\n    return 2\n", encoding="utf-8")
    changed = asyncio.run(service.update_files(1, ["service.py"]))
    replaced = chunks_for(sessions, 1)

    assert first.chunks_written == 1
    assert second.chunks_written == 0
    assert [chunk.id for chunk in repeated] == [
        chunk.id for chunk in original
    ]
    assert changed.chunks_deleted == 1
    assert changed.chunks_written == 1
    assert replaced[0].content_hash != original[0].content_hash
    assert json.loads(replaced[0].embedding_json)


def test_full_index_removes_deleted_files(index_environment):
    sessions, service, _, workspace, _ = index_environment
    source = workspace / "obsolete.py"
    source.write_text("value = 1\n", encoding="utf-8")
    asyncio.run(service.index_repository(1))
    source.unlink()

    summary = asyncio.run(service.index_repository(1))

    assert summary.files_deleted == 1
    assert summary.chunks_deleted == 1
    assert chunks_for(sessions, 1) == []


def test_repository_indexes_are_isolated(index_environment):
    sessions, service, _, first_workspace, second_workspace = (
        index_environment
    )
    (first_workspace / "same.py").write_text(
        "value = 1\n",
        encoding="utf-8",
    )
    (second_workspace / "same.py").write_text(
        "value = 2\n",
        encoding="utf-8",
    )

    asyncio.run(service.index_repository(1))
    asyncio.run(service.index_repository(2))
    asyncio.run(service.update_files(1, ["same.py"]))

    assert len(chunks_for(sessions, 1)) == 1
    assert len(chunks_for(sessions, 2)) == 1
    assert (
        chunks_for(sessions, 1)[0].content
        != chunks_for(sessions, 2)[0].content
    )


def test_embedding_failure_rolls_back_without_partial_changes(
    index_environment,
):
    sessions, service, provider, workspace, _ = index_environment
    source = workspace / "service.py"
    source.write_text("value = 1\n", encoding="utf-8")
    asyncio.run(service.index_repository(1))
    original = chunks_for(sessions, 1)
    source.write_text("value = 2\n", encoding="utf-8")
    provider.fail = True

    with pytest.raises(RuntimeError, match="embedding unavailable"):
        asyncio.run(service.update_files(1, ["service.py"]))

    remaining = chunks_for(sessions, 1)
    assert len(remaining) == 1
    assert remaining[0].id == original[0].id
    assert remaining[0].content_hash == original[0].content_hash


def test_repository_clone_success_dispatches_full_index(
    monkeypatch,
    tmp_path,
):
    dispatched = []

    class FakeDb:
        def add(self, repository):
            repository.id = 41

        def commit(self):
            return None

        def refresh(self, repository):
            return None

    async def clone_repository(user_id, repository_id, repo_url):
        assert (user_id, repository_id) == (7, 41)
        return tmp_path

    monkeypatch.setattr(
        repo_service,
        "workspace_service",
        SimpleNamespace(clone_repository=clone_repository),
    )
    monkeypatch.setattr(
        index_tasks.index_repository_task,
        "delay",
        lambda repository_id: dispatched.append(repository_id),
    )

    repository = asyncio.run(
        repo_service.create_repository(
            FakeDb(),
            user_id=7,
            name="demo",
            repo_url="https://example.com/demo.git",
            default_branch="main",
        )
    )

    assert repository.local_path == str(tmp_path)
    assert dispatched == [41]


def test_incremental_index_dispatch_is_non_blocking(monkeypatch):
    dispatched = []
    monkeypatch.setattr(
        index_tasks.update_repository_files_task,
        "delay",
        lambda repository_id, files: dispatched.append(
            (repository_id, files)
        ),
    )

    agent_tasks.dispatch_incremental_index(9, ["app/main.py"])

    assert dispatched == [(9, ["app/main.py"])]

    def fail_dispatch(repository_id, files):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        index_tasks.update_repository_files_task,
        "delay",
        fail_dispatch,
    )
    agent_tasks.dispatch_incremental_index(9, ["app/main.py"])
