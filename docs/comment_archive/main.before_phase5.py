import uvicorn
from fastapi import FastAPI

from app.core.config import settings
from app.db import base  # noqa: F401
from app.api import agents, auth, conversations, messages, tasks, websocket


app = FastAPI(title=settings.app_name)

app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(conversations.router, prefix="/api/conversations", tags=["会话"])
app.include_router(messages.router, prefix="/api", tags=["消息"])
app.include_router(agents.router, prefix="/api/agents", tags=["Agent"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["任务"])
app.include_router(websocket.router, tags=["实时聊天"])


@app.get("/health", tags=["系统"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


# 允许直接运行 python -m app.main 启动开发服务。
if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)

