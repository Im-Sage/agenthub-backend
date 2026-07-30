from typing import Annotated, TypedDict, List, Dict, Any
import operator
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    # 消息历史，使用 operator.add 实现增量追加
    messages: Annotated[List[BaseMessage], operator.add]
    
    # 核心元数据
    task_id: int
    conversation_id: int
    repo_path: str | None
    repository_id: int | None
    user_id: int | None
    
    # 任务计划 (Orchestrator 生成)
    plan: List[Dict[str, Any]]
    current_step_index: int
    
    # 任务执行上下文 (给子 Agent 使用)
    current_agent: str | None
    current_instruction: str | None
    
    # 结果与自愈
    execution_results: Annotated[List[Dict[str, Any]], operator.add]
    verification_results: Annotated[List[Dict[str, Any]], operator.add]
    verification_attempts: int
    errors: List[str]
    
    # 状态标记
    awaiting_confirmation: bool
    approval_status: str | None
    is_finished: bool
    final_summary: str | None
    metadata_json: str | None # 用于存储额外元数据，如 child_ids
