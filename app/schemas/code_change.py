from datetime import datetime

from pydantic import BaseModel, Field


class CodeChangeGenerate(BaseModel):
    task_id: int
    repository_id: int


class CodeChangeRead(BaseModel):
    id: int
    task_id: int
    repository_id: int
    parent_code_change_id: int | None = None
    revision_index: int = 1
    repo_url: str
    branch_name: str
    commit_hash: str | None
    changed_files: str
    diff_text: str
    status: str
    reject_reason: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CodeChangeEvent(BaseModel):
    event: str = "code_change.generated"
    data: CodeChangeRead


class CodeChangeReject(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
