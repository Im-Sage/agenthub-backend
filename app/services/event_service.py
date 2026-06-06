from datetime import datetime
from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from app.core.broadcaster import broadcaster


def _encode_data(data: Any) -> Any:
    if isinstance(data, BaseModel):
        return data.model_dump(mode="json")
    return jsonable_encoder(data)


async def publish_event(conversation_id: int, event: str, data: Any) -> None:
    payload = {
        "event": event,
        "conversation_id": conversation_id,
        "data": _encode_data(data),
    }
    await broadcaster.publish(f"conv_{conversation_id}", payload)


async def publish_task_event(task, event: str = "task.updated") -> None:
    from app.schemas.task import TaskRead

    await publish_event(task.conversation_id, event, TaskRead.model_validate(task))


async def publish_task_log(task, message: str) -> None:
    await publish_event(
        task.conversation_id,
        "task.log",
        {
            "task_id": task.id,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


async def publish_message_event(message, event: str = "message.created") -> None:
    from app.schemas.message import MessageRead

    await publish_event(message.conversation_id, event, MessageRead.model_validate(message))


async def publish_code_change_event(conversation_id: int, code_change, event: str = "code_change.created") -> None:
    from app.schemas.code_change import CodeChangeRead

    await publish_event(conversation_id, event, CodeChangeRead.model_validate(code_change))


async def publish_pull_request_event(conversation_id: int, pull_request, event: str = "pull_request.created") -> None:
    from app.schemas.pull_request import PullRequestRead

    await publish_event(conversation_id, event, PullRequestRead.model_validate(pull_request))


async def publish_deployment_event(conversation_id: int, deployment, event: str = "deployment.created") -> None:
    from app.schemas.deployment import DeploymentRead

    await publish_event(conversation_id, event, DeploymentRead.model_validate(deployment))


async def publish_code_review_event(conversation_id: int, code_review, event: str = "code_review.created") -> None:
    from app.schemas.code_review import CodeReviewRead

    await publish_event(conversation_id, event, CodeReviewRead.model_validate(code_review))
