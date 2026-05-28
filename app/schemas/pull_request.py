from datetime import datetime

from pydantic import BaseModel, Field


class PullRequestCreate(BaseModel):
    code_change_id: int
    title: str = Field(default="AgentHub generated change", min_length=1, max_length=200)
    body: str | None = None


class PullRequestRead(BaseModel):
    id: int
    code_change_id: int
    task_id: int
    repository_id: int
    branch_name: str
    commit_hash: str
    title: str
    body: str | None
    pr_url: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PullRequestEvent(BaseModel):
    event: str = "pull_request.created"
    data: PullRequestRead

