import json
from app.core.logging import (
    get_logger,
    log_agent_event,
    safe_error_summary,
    sanitize_event_value,
)
from app.db.session import SessionLocal
from app.models.tool_call import ToolCall
from app.tools.base import ToolCallRequest, ToolCallResult


logger = get_logger("audit")


def _mask_arguments(arguments: dict) -> str:
    """脱敏敏感参数"""
    masked = {
        str(key): sanitize_event_value(str(key), value)
        for key, value in arguments.items()
    }
    return json.dumps(masked, ensure_ascii=False)


def record_tool_call(
    request: ToolCallRequest,
    result: ToolCallResult,
    risk_level: str
) -> None:
    """将工具调用结果记录到数据库"""
    db = SessionLocal()
    try:
        call_record = ToolCall(
            task_id=request.task_id,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            tool_name=request.name,
            risk_level=risk_level,
            arguments_json=_mask_arguments(request.arguments),
            result_json=(
                json.dumps(
                    sanitize_event_value(
                        "structured_content",
                        result.structured_content,
                    ),
                    ensure_ascii=False,
                )
                if result.success
                else None
            ),
            success=result.success,
            error_message=safe_error_summary(result.error),
        )
        db.add(call_record)
        db.commit()
    except Exception as exc:
        log_agent_event(
            logger,
            "audit.failed",
            task_id=request.task_id,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            tool_name=request.name,
            success=False,
            error_type=type(exc).__name__,
        )
    finally:
        db.close()
