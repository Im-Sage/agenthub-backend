from datetime import datetime

from pydantic import BaseModel, Field


class RepositoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    repo_url: str = Field(min_length=1, max_length=500)
    default_branch: str = Field(default="main", min_length=1, max_length=100)


class RepositoryRead(BaseModel):
    id: int
    user_id: int
    name: str
    repo_url: str
    local_path: str
    default_branch: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

