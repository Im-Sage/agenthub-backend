from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class MessageType(str, Enum):
    TEXT = "text"
    TASK = "task"
    DIFF = "diff"
    DEPLOY = "deploy"


class SenderType(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class CodeChangeStatus(str, Enum):
    GENERATED = "generated"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMMITTED = "committed"


class DeploymentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class AgentAdapterType(str, Enum):
    MOCK = "mock"
    QWEN = "qwen"
    ORCHESTRATOR = "orchestrator"
    OPENAI = "openai"
    CLAUDE = "claude"
