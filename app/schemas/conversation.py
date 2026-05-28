from datetime import datetime

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    type: str = Field(default="single", pattern="^(single|group)$")


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationRead(BaseModel):
    id: int
    user_id: int
    title: str
    type: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

