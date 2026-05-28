from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# 用于注册/创建用户的请求体验证。对 username、email、password 做类型和长度/格式约束。
class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=50)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    # 若为 tax: float | None = None （代表tax为可选字段）则会导致 FastAPI 在请求体中缺少 tax 字段时抛出 422 错误，因为 Pydantic 认为 tax 是必填字段。
    # 通过设置默认值 None，Pydantic 会将 tax 视为可选字段，在请求体中缺少 tax 字段时会自动将其设置为 None，而不会抛出错误。
    # tax: float | None = None


class UserLogin(BaseModel):
    username: str
    password: str

# 用于响应模型，包含了 User 模型的所有字段，并且通过 model_config 设置了 from_attributes=True，使得 Pydantic 可以直接从 SQLAlchemy 模型实例中读取属性值进行序列化。
# 响应给前端的数据结构。
class UserRead(BaseModel):
    id: int
    username: str
    email: EmailStr
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenRead(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead

