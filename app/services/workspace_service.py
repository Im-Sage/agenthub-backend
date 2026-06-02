import os
import shutil
from pathlib import Path
from git import Repo, GitCommandError

from app.core.config import PROJECT_ROOT


class WorkspaceError(Exception):
    pass


class WorkspaceService:
    def __init__(self, workspaces_dir: Path | str = None):
        self.workspaces_dir = Path(workspaces_dir) if workspaces_dir else PROJECT_ROOT / "workspaces"
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)

    def get_repo_path(self, repo_id: int) -> Path:
        return self.workspaces_dir / f"repo-{repo_id}"

    def ensure_git_repo(self, path: Path) -> None:
        if not (path / ".git").exists():
            raise WorkspaceError("目标路径不是 Git 仓库")

    def validate_path(self, local_path: str, target_file: str) -> Path:
        """检查路径是否安全，防止跳出工作空间或访问敏感文件"""
        workspace_path = Path(local_path).resolve()
        target_path = (workspace_path / target_file).resolve()

        if not str(target_path).startswith(str(workspace_path)):
            raise WorkspaceError(f"安全限制：尝试访问工作空间外部的路径 {target_file}")
            
        sensitive_files = [".env", ".git"]
        for sensitive in sensitive_files:
            if sensitive in target_path.parts:
                raise WorkspaceError(f"安全限制：禁止访问敏感文件或目录 {sensitive}")
                
        return target_path

    def write_file(self, local_path: str, target_file: str, content: str) -> None:
        """安全地写入文件"""
        target_path = self.validate_path(local_path, target_file)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")

    def clone_repository(self, repo_id: int, repo_url: str) -> Path:
        """克隆仓库到指定工作空间"""
        workspace_path = self.get_repo_path(repo_id)
        if workspace_path.exists():
            self.ensure_git_repo(workspace_path)
            return workspace_path
        
        source_path = Path(repo_url)
        try:
            if source_path.exists():
                self.ensure_git_repo(source_path)
                Repo.clone_from(str(source_path), str(workspace_path))
            else:
                Repo.clone_from(repo_url, str(workspace_path))
            return workspace_path
        except GitCommandError as e:
            raise WorkspaceError(f"无法克隆仓库: {e}")

    def prepare_branch(self, local_path: str, default_branch: str, branch_name: str) -> None:
        """创建并切换到任务专用的分支"""
        workspace_path = Path(local_path)
        self.ensure_git_repo(workspace_path)
        
        try:
            repo = Repo(workspace_path)
            # 1. 强制清理未提交的杂质，确保 Agent 环境纯净
            repo.git.reset('--hard')
            repo.git.clean('-fd')
            
            # 2. 尝试切换到基准分支
            try:
                repo.git.checkout(default_branch)
            except GitCommandError:
                # 如果失败，可能是空仓库或者分支名不对，记录日志但继续
                print(f"[Workspace] Could not checkout base branch {default_branch}, proceeding with current state.")

            # 3. 创建或重置任务专用分支
            repo.git.checkout('-B', branch_name)
        except GitCommandError as e:
            raise WorkspaceError(f"分支操作失败。原始错误: {e}")

    def get_diff(self, local_path: str) -> str:
        """获取工作空间的 git diff"""
        workspace_path = Path(local_path)
        self.ensure_git_repo(workspace_path)
        
        try:
            repo = Repo(workspace_path)
            # 强制将所有变更（包括 untracked 文件）添加到暂存区
            repo.git.add(A=True)
            
            # 使用 --cached 对比暂存区和 HEAD，确保新添加的文件也能被 diff 捕捉到
            diff = repo.git.diff("--cached")
            return diff
        except GitCommandError as e:
            raise WorkspaceError(f"获取 diff 失败: {e}")

    def get_changed_files(self, local_path: str) -> list[str]:
        """获取修改过的文件列表"""
        workspace_path = Path(local_path)
        self.ensure_git_repo(workspace_path)
        
        try:
            repo = Repo(workspace_path)
            # 强制加入暂存区，确保 untracked 被捕获
            repo.git.add(A=True)
            
            # 使用 --cached 获取所有被暂存的修改和新增文件
            changed_files = repo.git.diff('--name-only', '--cached').splitlines()
            
            return [f for f in changed_files if f]
        except GitCommandError as e:
            raise WorkspaceError(f"获取变更文件列表失败: {e}")

    def commit_changes(self, local_path: str, commit_message: str) -> str:
        """提交工作空间的变更"""
        workspace_path = Path(local_path)
        self.ensure_git_repo(workspace_path)
        
        try:
            repo = Repo(workspace_path)
            repo.git.config("user.email", "agenthub@example.com")
            repo.git.config("user.name", "AgentHub")
            repo.git.add(A=True)
            
            # Check if there's anything to commit
            if not repo.is_dirty(untracked_files=True) and not repo.index.diff("HEAD"):
                raise WorkspaceError("没有可提交的变更")
                
            repo.index.commit(commit_message)
            return repo.head.commit.hexsha
        except GitCommandError as e:
            raise WorkspaceError(f"提交变更失败: {e}")

    def get_commit_hash(self, local_path: str) -> str:
        workspace_path = Path(local_path)
        self.ensure_git_repo(workspace_path)
        try:
            repo = Repo(workspace_path)
            # 检查是否有提交
            try:
                repo.git.rev_parse('--verify', 'HEAD')
                return repo.head.commit.hexsha
            except (GitCommandError, ValueError):
                # 没有任何提交，返回一个占位哈希
                return "0000000000000000000000000000000000000000"
        except Exception as e:
            raise WorkspaceError(f"获取 commit hash 失败: {e}")

    def push_branch(self, local_path: str, branch_name: str) -> None:
        """推送分支到远程仓库"""
        workspace_path = Path(local_path)
        self.ensure_git_repo(workspace_path)
        
        try:
            repo = Repo(workspace_path)
            origin = repo.remote(name='origin')
            # 使用 settings 中的 github_token 构造带 auth 的 URL 来推送 (仅支持 HTTPS)
            from app.core.config import settings
            if settings.github_token:
                remote_url = origin.url
                if remote_url.startswith("https://"):
                    auth_url = remote_url.replace("https://", f"https://x-access-token:{settings.github_token}@")
                    origin.set_url(auth_url)
                    
            origin.push(refspec=f'{branch_name}:{branch_name}')
            
            # 恢复原始 URL 以免泄露 Token
            if settings.github_token and remote_url.startswith("https://"):
                origin.set_url(remote_url)
                
        except GitCommandError as e:
            raise WorkspaceError(f"推送分支失败: {e}")

workspace_service = WorkspaceService()
