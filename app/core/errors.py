from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


DEFAULT_ERROR_CODES = {
    status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
    status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
    status.HTTP_403_FORBIDDEN: "FORBIDDEN",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_409_CONFLICT: "CONFLICT",
    status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
    status.HTTP_422_UNPROCESSABLE_ENTITY: "VALIDATION_ERROR",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "INTERNAL_SERVER_ERROR",
}


DETAIL_CODE_HINTS = {
    "Task not found": "TASK_NOT_FOUND",
    "Conversation not found": "CONVERSATION_NOT_FOUND",
    "Repository not found": "REPOSITORY_NOT_FOUND",
    "CodeChange not found": "CODE_CHANGE_NOT_FOUND",
    "CodeChange must be": "CODE_CHANGE_INVALID_STATUS",
    "Task must be FAILED": "TASK_INVALID_STATUS",
    "Task plan must be awaiting confirmation": "TASK_PLAN_INVALID_STATUS",
    "Task plan is empty": "TASK_PLAN_EMPTY",
    "用户名或邮箱已存在": "USER_ALREADY_EXISTS",
    "用户名或密码错误": "INVALID_CREDENTIALS",
    "登录状态无效或已过期": "INVALID_TOKEN",
}


def error_code_for(status_code: int, detail: Any) -> str:
    if isinstance(detail, str):
        for needle, code in DETAIL_CODE_HINTS.items():
            if needle in detail:
                return code
    return DEFAULT_ERROR_CODES.get(status_code, "ERROR")


def build_error_payload(
    *,
    status_code: int,
    detail: Any,
    code: str | None = None,
    errors: Sequence[Any] | None = None,
) -> dict[str, Any]:
    message = detail if isinstance(detail, str) else "Request failed"
    payload = {
        "detail": detail,
        "error": {
            "code": code or error_code_for(status_code, detail),
            "message": message,
            "detail": {
                "status_code": status_code,
            },
        },
    }
    if errors is not None:
        payload["error"]["detail"]["errors"] = list(errors)
    return payload


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=build_error_payload(status_code=exc.status_code, detail=exc.detail),
        headers=exc.headers,
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=build_error_payload(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Request validation failed",
            code="VALIDATION_ERROR",
            errors=exc.errors(),
        ),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=build_error_payload(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
            code="INTERNAL_SERVER_ERROR",
        ),
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
