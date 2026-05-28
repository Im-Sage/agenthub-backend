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
    preview_url: str
    status: str
    logs: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DeploymentEvent(BaseModel):
    event: str = "deployment.created"
    data: DeploymentRead

