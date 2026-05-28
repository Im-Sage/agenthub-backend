from datetime import datetime

from pydantic import BaseModel


class AgentRead(BaseModel):
    id: int
    name: str
    code: str
    adapter_type: str
    system_prompt: str | None
    capabilities: str | None
    enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}

