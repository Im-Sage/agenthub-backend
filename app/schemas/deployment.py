from datetime import datetime

from pydantic import BaseModel, Field


class DeploymentCreate(BaseModel):
    code_change_id: int
    provider: str = Field(default="local", min_length=1, max_length=50)


class DeploymentRead(BaseModel):
    id: int
    task_id: int
    code_change_id: int
    provider: str
    external_id: str | None = None
    preview_url: str | None = None
    status: str
    build_logs: str | None = None
    deploy_logs: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DeploymentEvent(BaseModel):
    event: str = "deployment.created"
    data: DeploymentRead

