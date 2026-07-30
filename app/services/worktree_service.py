import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from git import GitCommandError, Repo

from app.core.config import settings
from app.services.repository_lock_service import repository_git_lock


@dataclass(frozen=True)
class WorktreeHandle:
    path: str
    branch_name: str
    base_commit_hash: str


@dataclass(frozen=True)
class StepCommit:
    commit_hash: str | None
    changed_files: tuple[str, ...]
    diff_text: str
    has_changes: bool


@dataclass(frozen=True)
class MergeResult:
    success: bool
    commit_hash: str | None
    conflict_files: tuple[str, ...]
    error: str | None


LockFactory = Callable[[int], AbstractContextManager]


class WorktreeService:
    def __init__(
        self,
        *,
        repository_id: int,
        user_id: int,
        repository_path: str | Path,
        worktree_root: str | Path | None = None,
        lock_factory: LockFactory = repository_git_lock,
    ):
        self.repository_id = repository_id
        self.user_id = user_id
        self.repository_path = Path(repository_path).resolve()
        self.worktree_root = Path(
            worktree_root or settings.resolved_agent_worktree_root
        ).resolve()
        self._lock_factory = lock_factory
        self._repo = Repo(self.repository_path)

    def close(self) -> None:
        self._repo.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def resolve_base_commit(self, revision: str = "HEAD") -> str:
        return self._repo.commit(revision).hexsha

    def _orchestrator_root(self, parent_task_id: int) -> Path:
        return (
            self.worktree_root
            / f"user-{self.user_id}"
            / f"repo-{self.repository_id}"
            / f"orchestrator-{parent_task_id}"
        )

    def _validated_worktree_path(self, path: str | Path) -> Path:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.worktree_root):
            raise ValueError(
                f"Path is outside configured worktree root: {resolved}"
            )
        return resolved

    def _registered_worktrees(self) -> set[Path]:
        output = self._repo.git.worktree("list", "--porcelain")
        return {
            Path(line.removeprefix("worktree ")).resolve()
            for line in output.splitlines()
            if line.startswith("worktree ")
        }

    def _branch_exists(self, branch_name: str) -> bool:
        return any(
            head.name == branch_name
            for head in self._repo.heads
        )

    def _ensure_worktree(
        self,
        *,
        path: Path,
        branch_name: str,
        base_commit_hash: str,
    ) -> WorktreeHandle:
        path = self._validated_worktree_path(path)
        with self._lock_factory(self.repository_id):
            registered = self._registered_worktrees()
            if path in registered:
                with Repo(path) as worktree_repo:
                    if worktree_repo.active_branch.name != branch_name:
                        raise RuntimeError(
                            f"Worktree branch mismatch for {path}"
                        )
                return WorktreeHandle(
                    path=str(path),
                    branch_name=branch_name,
                    base_commit_hash=base_commit_hash,
                )
            if path.exists():
                raise RuntimeError(
                    f"Unregistered worktree path already exists: {path}"
                )

            path.parent.mkdir(parents=True, exist_ok=True)
            if self._branch_exists(branch_name):
                self._repo.git.worktree(
                    "add",
                    str(path),
                    branch_name,
                )
            else:
                self._repo.git.worktree(
                    "add",
                    "-b",
                    branch_name,
                    str(path),
                    base_commit_hash,
                )
        return WorktreeHandle(
            path=str(path),
            branch_name=branch_name,
            base_commit_hash=base_commit_hash,
        )

    def ensure_integration_worktree(
        self,
        parent_task_id: int,
        base_commit_hash: str,
    ) -> WorktreeHandle:
        return self._ensure_worktree(
            path=self._orchestrator_root(parent_task_id) / "integration",
            branch_name=(
                f"agent/orchestrator-{parent_task_id}/integration"
            ),
            base_commit_hash=base_commit_hash,
        )

    def ensure_step_worktree(
        self,
        parent_task_id: int,
        step_key: str,
        base_commit_hash: str,
    ) -> WorktreeHandle:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", step_key):
            raise ValueError(f"Invalid orchestrator step key: {step_key!r}")
        return self._ensure_worktree(
            path=(
                self._orchestrator_root(parent_task_id)
                / "steps"
                / step_key
            ),
            branch_name=(
                f"agent/orchestrator-{parent_task_id}/{step_key}"
            ),
            base_commit_hash=base_commit_hash,
        )

    def reset_step_worktree(
        self,
        handle: WorktreeHandle,
        base_commit_hash: str,
    ) -> None:
        path = self._validated_worktree_path(handle.path)
        if "steps" not in path.relative_to(self.worktree_root).parts:
            raise ValueError("Reset is only allowed for a step worktree")
        with Repo(path) as worktree_repo:
            worktree_repo.git.reset("--hard", base_commit_hash)
            worktree_repo.git.clean("-fd")

    def commit_step_changes(
        self,
        handle: WorktreeHandle,
        message: str,
    ) -> StepCommit:
        path = self._validated_worktree_path(handle.path)
        with Repo(path) as worktree_repo:
            worktree_repo.git.add(A=True)
            changed_files = tuple(
                line
                for line in worktree_repo.git.diff(
                    "--cached",
                    "--name-only",
                ).splitlines()
                if line
            )
            if not changed_files:
                return StepCommit(
                    commit_hash=None,
                    changed_files=(),
                    diff_text="",
                    has_changes=False,
                )
            diff_text = worktree_repo.git.diff("--cached")
            commit = worktree_repo.index.commit(message)
        return StepCommit(
            commit_hash=commit.hexsha,
            changed_files=changed_files,
            diff_text=diff_text,
            has_changes=True,
        )

    def merge_step_commit(
        self,
        integration: WorktreeHandle,
        commit_hash: str | None,
    ) -> MergeResult:
        if commit_hash is None:
            return MergeResult(
                success=True,
                commit_hash=None,
                conflict_files=(),
                error=None,
            )

        path = self._validated_worktree_path(integration.path)
        with Repo(path) as integration_repo:
            with self._lock_factory(self.repository_id):
                try:
                    integration_repo.git.cherry_pick(commit_hash)
                except GitCommandError as exc:
                    conflict_files = tuple(
                        line
                        for line in integration_repo.git.diff(
                            "--name-only",
                            "--diff-filter=U",
                        ).splitlines()
                        if line
                    )
                    cherry_pick_head = (
                        Path(integration_repo.git_dir)
                        / "CHERRY_PICK_HEAD"
                    )
                    if cherry_pick_head.exists():
                        integration_repo.git.cherry_pick("--abort")
                    return MergeResult(
                        success=False,
                        commit_hash=None,
                        conflict_files=conflict_files,
                        error=str(exc),
                    )
            merged_commit_hash = integration_repo.head.commit.hexsha
            return MergeResult(
                success=True,
                commit_hash=merged_commit_hash,
                conflict_files=(),
                error=None,
            )

    def diff_between(
        self,
        worktree_path: str | Path,
        base_commit_hash: str,
        result_commit_hash: str | None,
    ) -> StepCommit:
        path = self._validated_worktree_path(worktree_path)
        if result_commit_hash is None:
            return StepCommit(None, (), "", False)
        with Repo(path) as worktree_repo:
            revision_range = f"{base_commit_hash}..{result_commit_hash}"
            changed_files = tuple(
                line
                for line in worktree_repo.git.diff(
                    "--name-only",
                    revision_range,
                ).splitlines()
                if line
            )
            return StepCommit(
                commit_hash=result_commit_hash,
                changed_files=changed_files,
                diff_text=worktree_repo.git.diff(revision_range),
                has_changes=bool(changed_files),
            )

    def remove_worktree(self, worktree_path: str | Path) -> None:
        path = self._validated_worktree_path(worktree_path)
        with self._lock_factory(self.repository_id):
            if path in self._registered_worktrees():
                self._repo.git.worktree(
                    "remove",
                    "--force",
                    str(path),
                )
            elif path.exists():
                raise RuntimeError(
                    f"Refusing to remove unregistered path: {path}"
                )

    def cleanup_step_branch(self, branch_name: str) -> None:
        if (
            not branch_name.startswith("agent/orchestrator-")
            or branch_name.endswith("/integration")
        ):
            raise ValueError(
                f"Refusing to delete non-step branch: {branch_name}"
            )
        with self._lock_factory(self.repository_id):
            if self._branch_exists(branch_name):
                self._repo.git.branch("-D", branch_name)

    def prune(self) -> None:
        with self._lock_factory(self.repository_id):
            self._repo.git.worktree("prune")
