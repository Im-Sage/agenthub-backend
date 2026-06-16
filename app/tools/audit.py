import json
from app.db.session import SessionLocal
from app.models.tool_call import ToolCall
from app.tools.base import ToolCallRequest, ToolCallResult


def _mask_arguments(arguments: dict) -> str:
    """脱敏敏感参数"""
    sensitive_keys = ["token", "password", "secret", "api_key", "authorization"]
    masked = {}
    for k, v in arguments.items():
        if any(sk in k.lower() for sk in sensitive_keys):
            masked[k] = "******"
        else:
            masked[k] = v
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
            result_json=json.dumps(result.structured_content, ensure_ascii=False) if result.success else None,
            success=result.success,
            error_message=result.error
        )
        db.add(call_record)
        db.commit()
    except Exception as e:
        print(f"[Audit] Failed to record tool call: {e}")
    finally:
        db.close()
