import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.tool_calling import AGENT_TOOL_PROFILES
from app.models.code_chunk import CodeChunk
from app.models.repository import Repository
from app.models.user import Base, User
from app.mcp.repository_resolver import ResolvedWorkspace
from app.rag.retrieval import (
    HybridCodeRetriever,
    RetrievedCodeChunk,
    cosine_similarity,
)
from app.tools.base import ToolCallRequest
from app.tools.rag_tools import register_rag_tools
from app.tools.registry import ToolRegistry


class FakeResolver:
    def __init__(self, workspace):
        self.workspace = workspace
        self.calls = []

    def resolve_owned_workspace(self, repository_id, user_id):
        self.calls.append((repository_id, user_id))
        return ResolvedWorkspace(
            repository_id=repository_id,
            user_id=user_id,
            local_path=str(self.workspace),
        )


class FakeEmbeddingProvider:
    def __init__(self, query_vector):
        self.query_vector = query_vector

    async def embed_query(self, text):
        return list(self.query_vector)

    async def embed_documents(self, texts):
        raise AssertionError("retrieval must not embed documents")


class FakeWorkspaceSearch:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def search_code(self, local_path, query, **kwargs):
        self.calls.append((local_path, query, kwargs))
        return list(self.results)


@pytest.fixture
def retrieval_environment(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(
            username="owner",
            email="owner@example.com",
            password_hash="hash",
        )
        other = User(
            username="other",
            email="other@example.com",
            password_hash="hash",
        )
        db.add_all([owner, other])
        db.flush()
        db.add_all(
            [
                Repository(
                    id=1,
                    user_id=owner.id,
                    name="one",
                    repo_url="https://example.com/one.git",
                    local_path=str(tmp_path),
                    default_branch="main",
                ),
                Repository(
                    id=2,
                    user_id=other.id,
                    name="two",
                    repo_url="https://example.com/two.git",
                    local_path=str(tmp_path),
                    default_branch="main",
                ),
            ]
        )
        db.add_all(
            [
                CodeChunk(
                    repository_id=1,
                    file_path="app/alpha.py",
                    language="python",
                    symbol_name="alpha",
                    chunk_type="function",
                    start_line=10,
                    end_line=10,
                    content="def alpha(): return 'alpha'",
                    content_hash="a" * 64,
                    embedding_json=json.dumps([1.0, 0.0]),
                ),
                CodeChunk(
                    repository_id=1,
                    file_path="app/beta.py",
                    language="python",
                    symbol_name="beta",
                    chunk_type="function",
                    start_line=20,
                    end_line=21,
                    content="def beta():\n    return 'beta'",
                    content_hash="b" * 64,
                    embedding_json=json.dumps([0.8, 0.2]),
                ),
                CodeChunk(
                    repository_id=1,
                    file_path="app/empty.py",
                    language="python",
                    symbol_name=None,
                    chunk_type="line_window",
                    start_line=1,
                    end_line=1,
                    content="empty",
                    content_hash="c" * 64,
                    embedding_json="[]",
                ),
                CodeChunk(
                    repository_id=2,
                    file_path="private/secret.py",
                    language="python",
                    symbol_name="secret",
                    chunk_type="function",
                    start_line=1,
                    end_line=1,
                    content="repository two secret",
                    content_hash="d" * 64,
                    embedding_json=json.dumps([1.0, 0.0]),
                ),
            ]
        )
        db.commit()
    return sessions, FakeResolver(tmp_path)


def make_retriever(
    retrieval_environment,
    *,
    keyword_results=None,
    query_vector=(1.0, 0.0),
):
    sessions, resolver = retrieval_environment
    keyword = FakeWorkspaceSearch(keyword_results)
    retriever = HybridCodeRetriever(
        session_factory=sessions,
        repository_resolver=resolver,
        embedding_provider=FakeEmbeddingProvider(query_vector),
        workspace_search=keyword,
    )
    return retriever, resolver, keyword


def test_cosine_similarity_orders_vectors_and_guards_invalid_inputs():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    with pytest.raises(ValueError, match="dimension"):
        cosine_similarity([1.0], [1.0, 0.0])


def test_vector_results_are_ranked_and_repository_isolated(
    retrieval_environment,
):
    retriever, resolver, _ = make_retriever(retrieval_environment)

    results = asyncio.run(
        retriever.search(
            repository_id=1,
            user_id=11,
            query="find alpha behavior",
        )
    )

    assert resolver.calls == [(1, 11)]
    assert [result.file_path for result in results] == [
        "app/alpha.py",
        "app/beta.py",
    ]
    assert all("secret" not in result.content for result in results)
    assert [result.vector_rank for result in results] == [1, 2]


def test_rrf_fuses_and_deduplicates_exact_chunk_identity(
    retrieval_environment,
):
    retriever, _, _ = make_retriever(
        retrieval_environment,
        keyword_results=[
            {
                "file": "app/alpha.py",
                "line": 10,
                "text": "def alpha(): return 'alpha'",
            },
            {
                "file": "README.md",
                "line": 2,
                "text": "alpha docs",
            },
        ],
    )

    results = asyncio.run(
        retriever.search(
            repository_id=1,
            user_id=11,
            query="alpha",
        )
    )

    alpha = next(
        result
        for result in results
        if result.file_path == "app/alpha.py"
    )
    assert alpha.keyword_rank == 1
    assert alpha.vector_rank == 1
    assert alpha.combined_score == pytest.approx(2.0 / 61.0)
    assert len(
        [
            result
            for result in results
            if (
                result.file_path,
                result.start_line,
                result.end_line,
            )
            == ("app/alpha.py", 10, 10)
        ]
    ) == 1


def test_retrieval_degrades_to_either_source_or_empty(
    retrieval_environment,
):
    sessions, resolver = retrieval_environment
    keyword_only = HybridCodeRetriever(
        session_factory=sessions,
        repository_resolver=resolver,
        embedding_provider=FakeEmbeddingProvider([]),
        workspace_search=FakeWorkspaceSearch(
            [{"file": "README.md", "line": 1, "text": "keyword"}]
        ),
    )
    assert [
        item.file_path
        for item in asyncio.run(
            keyword_only.search(
                repository_id=1,
                user_id=11,
                query="keyword",
            )
        )
    ] == ["README.md"]

    vector_only, _, _ = make_retriever(retrieval_environment)
    assert asyncio.run(
        vector_only.search(
            repository_id=1,
            user_id=11,
            query="semantic",
        )
    )

    empty_engine = create_engine("sqlite://")
    Base.metadata.create_all(empty_engine)
    empty_sessions = sessionmaker(bind=empty_engine)
    empty_retriever = HybridCodeRetriever(
        session_factory=empty_sessions,
        repository_resolver=resolver,
        embedding_provider=FakeEmbeddingProvider([1.0, 0.0]),
        workspace_search=FakeWorkspaceSearch(),
    )
    assert (
        asyncio.run(
            empty_retriever.search(
                repository_id=1,
                user_id=11,
                query="nothing",
            )
        )
        == []
    )


def test_query_top_k_and_content_budgets_are_enforced(
    retrieval_environment,
):
    retriever, _, _ = make_retriever(
        retrieval_environment,
        keyword_results=[
            {"file": "huge.txt", "line": 1, "text": "x" * 50_000}
        ],
    )

    with pytest.raises(ValueError, match="query"):
        asyncio.run(
            retriever.search(
                repository_id=1,
                user_id=11,
                query=" ",
            )
        )
    with pytest.raises(ValueError, match="top_k"):
        asyncio.run(
            retriever.search(
                repository_id=1,
                user_id=11,
                query="x",
                top_k=21,
            )
        )
    results = asyncio.run(
        retriever.search(
            repository_id=1,
            user_id=11,
            query="x",
            top_k=20,
        )
    )
    assert all(len(result.content) <= 8_000 for result in results)
    assert sum(len(result.content) for result in results) <= 40_000


def test_semantic_search_tool_hides_trusted_identity_parameters(
    monkeypatch,
):
    registry = ToolRegistry()
    register_rag_tools(registry)
    definition = registry.get_tool("workspace.semantic_search")

    assert set(definition.input_schema["properties"]) == {"query", "top_k"}
    assert set(definition.input_schema["required"]) == {"query"}
    assert all(
        "workspace.semantic_search" in AGENT_TOOL_PROFILES[agent]
        for agent in ("backend", "frontend", "reviewer", "qwen")
    )

    class FakeRetriever:
        async def search(self, **kwargs):
            assert kwargs == {
                "repository_id": 5,
                "user_id": 7,
                "query": "find parser",
                "top_k": 3,
            }
            return [
                RetrievedCodeChunk(
                    file_path="app/parser.py",
                    start_line=1,
                    end_line=2,
                    symbol_name="parse",
                    content="def parse(): pass",
                    keyword_rank=None,
                    vector_rank=1,
                    combined_score=1 / 61,
                )
            ]

    from app.tools import rag_tools

    monkeypatch.setattr(rag_tools, "hybrid_code_retriever", FakeRetriever())
    result = asyncio.run(
        registry.call(
            ToolCallRequest(
                name="workspace.semantic_search",
                arguments={"query": "find parser", "top_k": 3},
                repository_id=5,
                user_id=7,
            )
        )
    )

    assert result.success is True
    assert result.structured_content["results"][0]["symbol_name"] == "parse"
