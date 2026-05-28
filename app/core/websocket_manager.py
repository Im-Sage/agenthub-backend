from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: dict[int, list[WebSocket]] = defaultdict(list)

    # 连接到指定的 conversation_id，并将 WebSocket 添加到连接列表中
    async def connect(self, conversation_id: int, websocket: WebSocket) -> None:
        await websocket.accept() # 接受 WebSocket 连接请求
        """
        将 websocket 实例保存到按 conversation_id 分组的字典列表中后，
        当该对话中产生新消息时，系统就可以通过遍历该列表（如代码中的 broadcast_json 方法），
        准确地将消息广播给属于该对话的所有在线用户。
        这种设计使得系统能够高效地管理和维护多个对话的 WebSocket 连接，
        确保消息能够正确地发送给相关的用户，同时也方便了连接的添加和移除。
        """
        self._connections[conversation_id].append(websocket) # 将 WebSocket 添加到指定 conversation_id 的连接列表中


    # 从指定的 conversation_id 的连接列表中移除 WebSocket，
    # 如果该列表为空，则删除该 conversation_id 的键
    def disconnect(self, conversation_id: int, websocket: WebSocket) -> None:
        connections = self._connections.get(conversation_id, [])
        if websocket in connections:
            connections.remove(websocket)

        if not connections and conversation_id in self._connections:
            del self._connections[conversation_id]

    # 发送 JSON 数据到指定的 WebSocket 连接
    async def send_json(self, websocket: WebSocket, payload: dict[str, Any]) -> None:
        await websocket.send_json(payload) # 通过 WebSocket 连接发送 JSON 数据

    # 广播 JSON 数据到指定 conversation_id 的所有 WebSocket 连接，并处理断开连接的情况
    async def broadcast_json(self, conversation_id: int, payload: dict[str, Any]) -> None:
        disconnected: list[WebSocket] = []

        # self._connections.get(conversation_id, [])
        # 返回的是一个包含多个 WebSocket 实例的列表（代表当前在此对话中的所有在线用户的连接合集）。
        """
        每一个 websocket 实例代表一个单独的客户端连接。你可以将其理解为一个用户的实时连接。
        需要注意的是，如果同一个用户在多个浏览器标签页或多个设备上打开同一个对话，
        每个标签页或设备都会生成一个独立的 websocket 连接。
        """
        for websocket in self._connections.get(conversation_id, []):
            try:
                await websocket.send_json(payload) # 通过 WebSocket 连接发送 JSON 数据，如果发送失败（例如连接已断开），将该 WebSocket 添加到断开连接的列表中
            except RuntimeError:
                disconnected.append(websocket) # 如果发送失败（例如连接已断开），将该 WebSocket 添加到断开连接的列表中

        for websocket in disconnected:
            self.disconnect(conversation_id, websocket) # 从连接列表中移除断开连接的 WebSocket


websocket_manager = WebSocketManager() # 创建 WebSocketManager 实例，供整个应用程序使用，以便管理 WebSocket 连接和消息广播

