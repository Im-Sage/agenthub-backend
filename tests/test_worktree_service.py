from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from git import Repo

from app.services.worktree_service import (
    WorktreeHandle,
    WorktreeService,
)


def initialize_repository(path: Path) -> tuple[Repo, str]:
    path.mkdir()
    repo = Repo.init(path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "AgentHub Tests")
        config.set_value("user", "email", "agenthub@example.com")
    (path / "shared.txt").write_text("base\n", encoding="utf-8")
    repo.index.add(["shared.txt"])
    commit = repo.index.commit("initial")
    return repo, commit.hexsha


def head_commit(path: str) -> str:
    with Repo(path) as repo:
        return repo.head.commit.hexsha


@pytest.fixture
def short_tmp_path():
    with TemporaryDirectory(
        prefix=".wt-",
        dir=Path.cwd(),
    ) as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def worktree_fixture(short_tmp_path):
    repository_path = short_tmp_path / "repository"
    repo, base_commit = initialize_repository(repository_path)
    service = WorktreeService(
        repository_id=22,
        user_id=11,
        repository_path=repository_path,
        worktree_root=short_tmp_path / "task-worktrees",
        lock_factory=lambda repository_id: nullcontext(),
    )
    try:
        yield service, repo, repository_path, base_commit
    finally:
        service.close()
        repo.close()


def test_creates_distinct_integration_and_step_worktrees_from_same_base(
    worktree_fixture,
):
    service, repo, repository_path, base_commit = worktree_fixture

    integration = service.ensure_integration_worktree(
        parent_task_id=100,
        base_commit_hash=base_commit,
    )
    backend = service.ensure_step_worktree(
        parent_task_id=100,
        step_key="backend",
        base_commit_hash=base_commit,
    )
    frontend = service.ensure_step_worktree(
        parent_task_id=100,
        step_key="frontend",
        base_commit_hash=base_commit,
    )
    repeated_backend = service.ensure_step_worktree(
        parent_task_id=100,
        step_key="backend",
        base_commit_hash=base_commit,
    )

    assert len({integration.path, backend.path, frontend.path}) == 3
    assert repeated_backend == backend
    assert head_commit(integration.path) == base_commit
    assert head_commit(backend.path) == base_commit
    assert head_commit(frontend.path) == base_commit
    assert repo.head.commit.hexsha == base_commit
    assert repo.is_dirty(untracked_files=True) is False
    assert Path(repository_path, "shared.txt").read_text(
        encoding="utf-8"
    ) == "base\n"


def test_step_commit_and_cherry_pick_do_not_touch_shared_workspace(
    worktree_fixture,
):
    service, repo, repository_path, base_commit = worktree_fixture
    integration = service.ensure_integration_worktree(101, base_commit)
    step = service.ensure_step_worktree(101, "backend", base_commit)
    Path(step.path, "backend.txt").write_text("backend\n", encoding="utf-8")

    step_commit = service.commit_step_changes(
        step,
        "feat: backend step",
    )
    merge = service.merge_step_commit(
        integration,
        step_commit.commit_hash,
    )
    integrated_diff = service.diff_between(
        integration.path,
        base_commit,
        merge.commit_hash,
    )

    assert step_commit.has_changes is True
    assert step_commit.changed_files == ("backend.txt",)
    assert merge.success is True
    assert Path(integration.path, "backend.txt").read_text(
        encoding="utf-8"
    ) == "backend\n"
    assert integrated_diff.changed_files == ("backend.txt",)
    assert integrated_diff.has_changes is True
    assert not Path(repository_path, "backend.txt").exists()
    assert repo.head.commit.hexsha == base_commit


def test_commit_step_changes_skips_empty_commit(worktree_fixture):
    service, _, _, base_commit = worktree_fixture
    step = service.ensure_step_worktree(102, "review", base_commit)

    result = service.commit_step_changes(step, "review without changes")

    assert result.commit_hash is None
    assert result.changed_files == ()
    assert result.diff_text == ""
    assert result.has_changes is False


def test_conflicting_cherry_pick_records_files_and_preserves_first_result(
    worktree_fixture,
):
    service, _, _, base_commit = worktree_fixture
    integration = service.ensure_integration_worktree(103, base_commit)
    first = service.ensure_step_worktree(103, "first", base_commit)
    second = service.ensure_step_worktree(103, "second", base_commit)
    Path(first.path, "shared.txt").write_text("first\n", encoding="utf-8")
    Path(second.path, "shared.txt").write_text("second\n", encoding="utf-8")
    first_commit = service.commit_step_changes(first, "first").commit_hash
    second_commit = service.commit_step_changes(second, "second").commit_hash

    assert service.merge_step_commit(integration, first_commit).success is True
    conflict = service.merge_step_commit(integration, second_commit)

    assert conflict.success is False
    assert conflict.conflict_files == ("shared.txt",)
    assert conflict.error
    assert Path(integration.path, "shared.txt").read_text(
        encoding="utf-8"
    ) == "first\n"
    with Repo(integration.path) as integration_repo:
        assert not Path(
            integration_repo.git_dir,
            "CHERRY_PICK_HEAD",
        ).exists()
        assert integration_repo.is_dirty(untracked_files=True) is False


def test_reset_and_cleanup_are_idempotent_and_reject_outside_paths(
    worktree_fixture,
    short_tmp_path,
):
    service, _, _, base_commit = worktree_fixture
    step = service.ensure_step_worktree(104, "backend", base_commit)
    Path(step.path, "partial.txt").write_text("partial\n", encoding="utf-8")

    service.reset_step_worktree(step, base_commit)

    assert not Path(step.path, "partial.txt").exists()
    service.remove_worktree(step.path)
    service.remove_worktree(step.path)
    service.cleanup_step_branch(step.branch_name)
    service.cleanup_step_branch(step.branch_name)
    service.prune()
    assert not Path(step.path).exists()

    outside = short_tmp_path / "outside"
    outside.mkdir()
    unsafe = WorktreeHandle(
        path=str(outside),
        branch_name="agent/orchestrator-104/unsafe",
        base_commit_hash=base_commit,
    )
    with pytest.raises(ValueError, match="worktree root"):
        service.reset_step_worktree(unsafe, base_commit)


@pytest.mark.parametrize("use_posix_text", [False, True])
def test_accepts_windows_and_posix_path_text(
    short_tmp_path,
    use_posix_text,
):
    repository_path = short_tmp_path / "path-repository"
    repo, base_commit = initialize_repository(repository_path)
    root = short_tmp_path / "path-worktrees"
    repository_text = str(repository_path)
    root_text = str(root)
    if use_posix_text:
        repository_text = repository_path.as_posix()
        root_text = root.as_posix()
    service = WorktreeService(
        repository_id=2,
        user_id=1,
        repository_path=repository_text,
        worktree_root=root_text,
        lock_factory=lambda repository_id: nullcontext(),
    )

    try:
        handle = service.ensure_step_worktree(105, "backend", base_commit)

        assert Path(handle.path).is_dir()
        assert Path(handle.path).is_relative_to(root.resolve())
    finally:
        service.close()
        repo.close()
