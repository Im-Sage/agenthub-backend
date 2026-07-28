import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import AgentRunRequest
from app.agents.context.models import ContextSource
from app.agents.tool_calling import ToolCallingRunResult
from app.core.config import settings


class FakeContextAssembler:
    def __init__(self):
        self.calls = []

    async def assemble(self, **kwargs):
        self.calls.append(kwargs)
        return type(
            "Assembled",
            (),
            {
                "messages": [
                    SystemMessage(content=kwargs["system_prompt"]),
                    HumanMessage(content=kwargs["instruction"]),
                    SystemMessage(
                        content="Previous execution and errors:\n"
                        + "\n".join(kwargs["previous_errors"])
                    ),
                ],
                "estimated_tokens": 42,
                "blocks": [
                    type(
                        "Block",
                        (),
                        {"source": ContextSource.RETRIEVAL},
                    )()
                ],
                "truncated_blocks": [{}],
            },
        )()


@pytest.mark.anyio
async def test_qwen_adapter_delegates_to_native_tool_calling(
    monkeypatch,
):
    from app.agents import qwen_adapter

    fake_llm = object()
    captured = {}
    assembler = FakeContextAssembler()

    async def fake_run_tool_calling_agent(**kwargs):
        captured.update(kwargs)
        return ToolCallingRunResult(
            summary="Implemented the requested change.",
            changed_files=["app/main.py"],
            used_legacy_fallback=True,
        )

    monkeypatch.setattr(settings, "aliyun_api_key", "test-api-key")
    monkeypatch.setattr(qwen_adapter, "get_chat_llm", lambda: fake_llm)
    monkeypatch.setattr(qwen_adapter, "context_assembler", assembler)
    monkeypatch.setattr(
        qwen_adapter,
        "run_tool_calling_agent",
        fake_run_tool_calling_agent,
    )

    request = AgentRunRequest(
        task_id=11,
        conversation_id=22,
        instruction="Implement the application entry point.",
        repo_path="/trusted/workspace",
        context={
            "agent_code": "backend",
            "system_prompt": "You are the backend engineer.",
            "previous_error": "Previous syntax error",
        },
    )

    result = await qwen_adapter.QwenAgentAdapter().run(request)

    assert captured["llm"] is fake_llm
    assert captured["agent_code"] == "backend"
    assert captured["repo_path"] == "/trusted/workspace"
    assert captured["task_id"] == 11
    assert captured["conversation_id"] == 22

    messages = captured["messages"]
    assert isinstance(messages[0], SystemMessage)
    assert "You are the backend engineer." in messages[0].content
    assert "repository workspace tools" in messages[0].content
    assert "[FILE:]" in messages[0].content
    assert "Do not emit" in messages[0].content
    assert isinstance(messages[1], HumanMessage)
    assert messages[1].content == request.instruction
    assert isinstance(messages[2], SystemMessage)
    assert "Previous syntax error" in messages[2].content
    assert assembler.calls[0]["previous_errors"] == [
        "Previous syntax error"
    ]

    assert result.status == "success"
    assert result.summary == "Implemented the requested change."
    assert result.changed_files == ["app/main.py"]
    assert "provider=aliyun" in result.logs
    assert f"model={settings.aliyun_model}" in result.logs
    assert "files_changed=1" in result.logs
    assert "legacy_fallback=True" in result.logs
    assert "context_tokens=42" in result.logs
    assert "retrieval_chunks=1" in result.logs
    assert "truncated_blocks=1" in result.logs


@pytest.mark.anyio
async def test_qwen_adapter_defaults_agent_code_to_qwen(monkeypatch):
    from app.agents import qwen_adapter

    captured = {}
    assembler = FakeContextAssembler()

    async def fake_run_tool_calling_agent(**kwargs):
        captured.update(kwargs)
        return ToolCallingRunResult(summary="No changes required.")

    monkeypatch.setattr(settings, "aliyun_api_key", "test-api-key")
    monkeypatch.setattr(qwen_adapter, "get_chat_llm", object)
    monkeypatch.setattr(qwen_adapter, "context_assembler", assembler)
    monkeypatch.setattr(
        qwen_adapter,
        "run_tool_calling_agent",
        fake_run_tool_calling_agent,
    )

    await qwen_adapter.QwenAgentAdapter().run(
        AgentRunRequest(
            task_id=33,
            conversation_id=44,
            instruction="Answer the question.",
        )
    )

    assert captured["agent_code"] == "qwen"
    assert captured["repo_path"] is None


@pytest.mark.anyio
async def test_qwen_adapter_requires_aliyun_api_key(monkeypatch):
    from app.agents.qwen_adapter import QwenAgentAdapter

    monkeypatch.setattr(settings, "aliyun_api_key", None)

    with pytest.raises(
        RuntimeError,
        match="ALIYUN_API_KEY is not configured",
    ):
        await QwenAgentAdapter().run(
            AgentRunRequest(
                task_id=55,
                conversation_id=66,
                instruction="Answer the question.",
            )
        )
