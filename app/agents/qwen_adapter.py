import httpx
import re
from app.agents.base import AgentAdapter, AgentRunRequest, AgentRunResult
from app.core.config import settings
from app.services.workspace_service import workspace_service, WorkspaceError


class QwenAgentAdapter(AgentAdapter):
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        if not settings.aliyun_api_key:
            raise RuntimeError("未配置 ALIYUN_API_KEY，无法调用阿里通义千问。")

        # 基础 System Prompt
        base_system_prompt = request.context.get(
            "system_prompt",
            "你是 AgentHub 中的 AI 程序员。请用中文回答，保持简洁、专业。",
        )

        # 如果有工作区路径，注入开发者能力的指令
        if request.repo_path:
            base_system_prompt += (
                f"\n\n你现在正在操作一个真实的本地仓库，路径为：{request.repo_path}\n"
                "如果你需要修改或创建文件，请使用以下格式：\n"
                "[FILE: 相对路径]\n"
                "```代码语言\n"
                "代码内容\n"
                "```\n"
                "你可以一次性修改多个文件。请确保只输出必要的代码和简短的说明。"
            )

        payload = {
            "model": settings.aliyun_model,
            "messages": [
                {"role": "system", "content": base_system_prompt},
                {"role": "user", "content": request.instruction},
            ],
        }
        headers = {
            "Authorization": f"Bearer {settings.aliyun_api_key}",
            "Content-Type": "application/json",
        }
        url = f"{settings.aliyun_base_url.rstrip('/')}/chat/completions"

        async with httpx.AsyncClient(timeout=settings.aliyun_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        
        # 解析响应，提取文件变更
        changed_files = []
        if request.repo_path:
            changed_files = self._apply_file_changes(request.repo_path, content)

        return AgentRunResult(
            status="success",
            summary=content,
            changed_files=changed_files,
            logs=f"provider=aliyun model={settings.aliyun_model} files_changed={len(changed_files)}",
        )

    def _apply_file_changes(self, repo_path: str, content: str) -> list[str]:
        """解析 LLM 返回的内容并写入文件"""
        changed_files = []
        # 正则匹配 [FILE: path] 后接代码块的内容
        pattern = r"\[FILE:\s*(.+?)\]\s*\n\s*```.*?\n([\s\S]*?)\n```"
        matches = re.finditer(pattern, content)
        
        for match in matches:
            file_path = match.group(1).strip()
            file_content = match.group(2)
            try:
                workspace_service.write_file(repo_path, file_path, file_content)
                changed_files.append(file_path)
            except WorkspaceError as e:
                print(f"[Qwen] 写入文件 {file_path} 失败: {e}")
                
        return changed_files

