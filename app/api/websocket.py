from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.api.deps import get_user_from_token
from app.core.websocket_manager import websocket_manager
from app.db.session import SessionLocal
from app.models.conversation import Conversation


router = APIRouter()

"""
这个 WebSocket 路由的设计主要是为了实现一个基于对话 ID 的实时通信机制，
允许客户端通过 WebSocket 连接到特定的对话，并在该对话中接收实时消息更新。
以下是该路由的主要功能和设计考虑：  
1. 连接验证：在 WebSocket 连接建立时，路由会验证用户的身份和对话的有效性。
通过查询参数获取 token，并使用 get_user_from_token 函数验证用户身份。
同时检查对话是否存在且属于该用户，如果验证失败，则关闭 WebSocket 连接并返回相应的错误代码。
2. 连接管理：使用 WebSocketManager 类来管理 WebSocket 连接。每当一个新的 WebSocket 连接建立时，路由会将该连接注册到 WebSocketManager 中，以便后续能够向该连接发送消息。当连接断开时，路由会从 WebSocketManager 中注销该连接，确保资源的正确管理。
3. 实时通信：在连接建立后，路由进入一个无限循环，等待客户端发送消息。虽然当前实现中客户端发送的消息没有被处理，但这个设计为未来的功能扩展提供了基础，例如实现心跳机制或处理客户端发送的特定命令。同时，WebSocketManager 可以在对话中产生新消息时，向所有连接到该对话的客户端广播消息，实现实时更新的功能。
4. 数据库连接管理：在 WebSocket 路由中，手动管理数据库连接的生命周期。通过直接创建 SessionLocal 实例来获取数据库连接，并在 finally 块中确保连接被正确关闭。这种设计避免了在长连接中持续占用数据库连接，防止连接池耗尽，并确保无论连接正常结束还是异常断开，数据库资源都能得到正确清理。 
总的来说，这个 WebSocket 路由的设计旨在提供一个高效、可靠的实时通信机制，确保用户身份验证、连接管理和数据库资源的正确使用，为未来功能的扩展奠定了坚实的基础。
"""
@router.websocket("/ws/conversations/{conversation_id}")
async def conversation_websocket(
    websocket: WebSocket,
    conversation_id: int,
    token: str = Query(default=""), # 从查询参数中获取 token，默认值为空字符串，以防止缺少 token 导致的错误
) -> None:
    """
    在 WebSocket 路由中不使用 Depends(get_db) 主要是为了更精确地控制数据库连接的生命周期和异常处理：
    避免连接池耗尽：WebSocket 是长连接（基于 while True）。如果通过 Depends(get_db) 注入，整个长连接生命周期阶段都会持续占用一个数据库连接，在并发量高时很容易导致数据库连接池耗尽。
    生命周期控制：手动调用 SessionLocal() 并在 finally 块中显式调用 db.close()，可以更可靠地保证无论连接正常结束还是异常断开，数据库事务和连接都能被正确清理。
    """
    db: Session = SessionLocal()
    try:
        user = get_user_from_token(db, token)
        conversation = db.get(Conversation, conversation_id)
        if user is None or conversation is None or conversation.user_id != user.id:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        # 将 WebSocket 连接注册到 WebSocketManager 中，以便后续能够向该连接发送消息。当连接断开时，路由会从 WebSocketManager 中注销该连接，确保资源的正确管理。
        await websocket_manager.connect(conversation_id, websocket)
        # 连接建立后，路由进入一个无限循环，等待客户端发送消息。
        # 虽然当前实现中客户端发送的消息没有被处理，但这个设计为未来的功能扩展提供了基础，
        # 例如实现心跳机制或处理客户端发送的特定命令。同时，WebSocketManager
        # 可以在对话中产生新消息时，向所有连接到该对话的客户端广播消息，实现实时更新的功能。
        await websocket_manager.send_json(
            websocket,
            {
                "event": "connection.ready",
                "data": {
                    "conversation_id": conversation_id,
                    "user_id": user.id,
                },
            },
        )

        while True:
            # 第二阶段只负责服务端推送。这里读取客户端消息是为了维持连接并预留心跳扩展点。
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_manager.disconnect(conversation_id, websocket)
    finally:
        db.close()

