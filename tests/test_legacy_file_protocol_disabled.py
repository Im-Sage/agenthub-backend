import asyncio
from types import SimpleNamespace

from app.agents.graph import nodes
from app.agents.graph.schemas import VerificationCheck, VerificationResult
from app.core.config import Settings
from app.schemas.enums import CodeChangeStatus
from app.services.code_change_service import create_revision_task


class FakeDb:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)

    def commit(self):
        return None

    def refresh(self, value):
        value.id = 99


def test_legacy_file_protocol_is_disabled_by_default():
    assert (
        Settings.model_fields[
            "agent_legacy_file_protocol_fallback"
        ].default
        is False
    )


def test_revision_task_requires_workspace_tools_instead_of_markers():
    db = FakeDb()
    code_change = SimpleNamespace(
        id=7,
        status=CodeChangeStatus.REJECTED,
        reject_reason="Keep the public API compatible.",
    )
    source_task = SimpleNamespace(
        id=11,
        conversation_id=13,
        agent_id=17,
        instruction="Repair the service",
    )

    revision_task = create_revision_task(db, code_change, source_task)

    assert "Inspect the existing workspace state" in revision_task.instruction
    assert "Use the registered workspace tools" in revision_task.instruction
    assert "Run the available verification tools" in revision_task.instruction
    assert (
        "Do not emit [FILE:], [DELETE:], or [RENAME:] markers."
        in revision_task.instruction
    )
    assert "If files need to be changed, use" not in revision_task.instruction


def test_verifier_uses_changed_files_without_legacy_markers(monkeypatch):
    calls = []

    class FakeVerificationService:
        def verify(self, **kwargs):
            calls.append(kwargs)
            return VerificationResult(
                success=False,
                checks=[
                    VerificationCheck(
                        name="no_applicable_checks",
                        success=False,
                        exit_code=None,
                        summary="No applicable verification checks.",
                        duration_ms=0,
                    )
                ],
                failure_summary="No applicable verification checks.",
            )

    monkeypatch.setattr(
        nodes,
        "verification_service",
        FakeVerificationService(),
    )
    result = asyncio.run(
        nodes.verify_node(
            {
                "plan": [
                    {
                        "agent": "backend",
                        "instruction": "Write code for the service",
                    }
                ],
                "current_step_index": 0,
                "current_instruction": "Write code for the service",
                "execution_results": [
                    {"content": "Implemented the service", "files": []}
                ],
                "repo_path": "trusted-workspace",
                "repository_id": 23,
                "user_id": 29,
            }
        )
    )

    assert calls == [
        {
            "repository_id": 23,
            "user_id": 29,
            "changed_files": [],
            "instruction": "Write code for the service",
        }
    ]
    assert result["errors"] == ["No applicable verification checks."]
