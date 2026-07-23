from langchain_openai import ChatOpenAI

from app.core.config import settings


def get_chat_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.aliyun_model,
        openai_api_key=settings.aliyun_api_key,
        openai_api_base=settings.aliyun_base_url,
        timeout=settings.aliyun_timeout_seconds,
        temperature=0,
    )
