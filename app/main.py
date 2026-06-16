from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import (
    agents,
    auth,
    code_changes,
    conversations,
    deployments,
    messages,
    pull_requests,
    repos,
    tasks,
    websocket,
)
from app.core.config import PROJECT_ROOT, settings
from app.db import base  # noqa: F401
from app.tools import register_builtin_tools

# 注册内置工具
register_builtin_tools()
from app.core.broadcaster import broadcaster
from app.core.errors import install_error_handlers
from app.core.logging import RequestLoggingMiddleware, configure_logging
from app.core.websocket_manager import websocket_manager


PREVIEW_ROOT = PROJECT_ROOT / "previews"
PREVIEW_ROOT.mkdir(exist_ok=True)
configure_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时：开始订阅 Redis 频道
    await broadcaster.subscribe("conv_*", websocket_manager.broadcast_json)
    yield
    # 关闭时：清理连接
    await broadcaster.stop()

app = FastAPI(title=settings.app_name, lifespan=lifespan)
install_error_handlers(app)
app.add_middleware(RequestLoggingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(conversations.router, prefix="/api/conversations", tags=["会话"])
app.include_router(messages.router, prefix="/api", tags=["消息"])
app.include_router(agents.router, prefix="/api/agents", tags=["Agent"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["任务"])
app.include_router(repos.router, prefix="/api/repos", tags=["仓库"])
app.include_router(code_changes.router, prefix="/api/code-changes", tags=["代码变更"])
app.include_router(pull_requests.router, prefix="/api/pull-requests", tags=["PR"])
app.include_router(deployments.router, prefix="/api/deployments", tags=["部署"])
app.include_router(websocket.router, tags=["实时聊天"])
app.mount("/previews", StaticFiles(directory=PREVIEW_ROOT), name="previews")


@app.get("/health", tags=["系统"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)

