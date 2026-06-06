from datetime import datetime

from pydantic import BaseModel


class CodeReviewRead(BaseModel):
    id: int
    code_change_id: int
    task_id: int
    repository_id: int
    status: str
    risk_level: str
    summary: str
    findings_json: str
    recommendations_json: str
    raw_output: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CodeReviewEvent(BaseModel):
    event: str = "code_review.created"
    data: CodeReviewRead
