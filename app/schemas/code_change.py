from datetime import datetime

from pydantic import BaseModel


class CodeChangeGenerate(BaseModel):
    task_id: int
    repository_id: int


class CodeChangeRead(BaseModel):
    id: int
    task_id: int
    repository_id: int
    repo_url: str
    branch_name: str
    commit_hash: str | None
    changed_files: str
    diff_text: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class CodeChangeEvent(BaseModel):
    event: str = "code_change.generated"
    data: CodeChangeRead
