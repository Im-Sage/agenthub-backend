import re
from github import Github, Auth
from github.GithubException import GithubException

from app.core.config import settings

class GitHubError(Exception):
    pass

class GitHubService:
    def __init__(self):
        self._token = settings.github_token

    def _get_client(self) -> Github:
        if not self._token:
            raise GitHubError("GitHub token not configured in environment variables (.env).")
        auth = Auth.Token(self._token)
        return Github(auth=auth)

    def parse_repo_name(self, repo_url: str) -> str:
        """从 URL 解析出 owner/repo，支持 HTTPS 和 SSH 格式"""
        # HTTPS: https://github.com/owner/repo.git
        # SSH: git@github.com:owner/repo.git
        pattern = r"github\.com[:/]([^/]+)/([^/.]+)(\.git)?"
        match = re.search(pattern, repo_url)
        if match:
            return f"{match.group(1)}/{match.group(2)}"
        raise GitHubError(f"Could not parse GitHub owner/repo from URL: {repo_url}")

    def create_pull_request(self, repo_url: str, title: str, body: str, head_branch: str, base_branch: str) -> dict:
        """调用 GitHub API 创建真实的 Pull Request"""
        g = self._get_client()
        repo_name = self.parse_repo_name(repo_url)
        
        try:
            repo = g.get_repo(repo_name)
            
            # Check if PR already exists
            pulls = repo.get_pulls(state='open', head=f"{repo_name.split('/')[0]}:{head_branch}", base=base_branch)
            if pulls.totalCount > 0:
                pr = pulls[0]
                return {
                    "pr_number": pr.number,
                    "html_url": pr.html_url,
                    "state": pr.state,
                    "message": "PR already exists"
                }

            pr = repo.create_pull(
                title=title,
                body=body,
                head=head_branch,
                base=base_branch
            )
            return {
                "pr_number": pr.number,
                "html_url": pr.html_url,
                "state": pr.state,
                "message": "PR created successfully"
            }
        except GithubException as e:
            raise GitHubError(f"Failed to create PR via GitHub API: {e.data.get('message', str(e))}")

github_service = GitHubService()
