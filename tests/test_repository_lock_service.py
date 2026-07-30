import pytest

from app.services import repository_lock_service
from app.services.repository_lock_service import (
    RepositoryLockTimeout,
    repository_git_lock,
)


class FakeRedis:
    def __init__(self, *, acquire=True):
        self.acquire = acquire
        self.values = {}
        self.set_calls = []
        self.eval_calls = []

    def set(self, key, token, *, nx, ex):
        self.set_calls.append((key, token, nx, ex))
        if not self.acquire or (nx and key in self.values):
            return False
        self.values[key] = token
        return True

    def eval(self, script, key_count, key, token):
        self.eval_calls.append((script, key_count, key, token))
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0


def test_repository_git_lock_uses_set_nx_ex_and_token_release(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(
        repository_lock_service,
        "_get_redis_client",
        lambda: client,
    )
    monkeypatch.setattr(
        repository_lock_service.settings,
        "repository_git_lock_ttl_seconds",
        45,
    )

    with repository_git_lock(123):
        assert "lock:repository-git:123" in client.values

    assert len(client.set_calls) == 1
    key, token, nx, expiry = client.set_calls[0]
    assert (key, nx, expiry) == (
        "lock:repository-git:123",
        True,
        45,
    )
    assert client.eval_calls[0][1:] == (1, key, token)
    assert "get" in client.eval_calls[0][0]
    assert "del" in client.eval_calls[0][0]
    assert key not in client.values


def test_repository_git_lock_does_not_delete_replaced_token(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(
        repository_lock_service,
        "_get_redis_client",
        lambda: client,
    )

    with repository_git_lock(7):
        client.values["lock:repository-git:7"] = "new-owner-token"

    assert client.values["lock:repository-git:7"] == "new-owner-token"


def test_repository_git_lock_releases_after_body_exception(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(
        repository_lock_service,
        "_get_redis_client",
        lambda: client,
    )

    with pytest.raises(RuntimeError, match="body failed"):
        with repository_git_lock(9):
            raise RuntimeError("body failed")

    assert "lock:repository-git:9" not in client.values


def test_repository_git_lock_times_out_using_monotonic_clock(monkeypatch):
    client = FakeRedis(acquire=False)
    clock_values = iter([10.0, 10.0, 10.4, 11.1])
    sleeps = []
    monkeypatch.setattr(
        repository_lock_service,
        "_get_redis_client",
        lambda: client,
    )
    monkeypatch.setattr(
        repository_lock_service.settings,
        "repository_git_lock_timeout_seconds",
        1,
    )
    monkeypatch.setattr(
        repository_lock_service.time,
        "monotonic",
        lambda: next(clock_values),
    )
    monkeypatch.setattr(
        repository_lock_service.time,
        "sleep",
        sleeps.append,
    )

    with pytest.raises(
        RepositoryLockTimeout,
        match="repository 55",
    ):
        with repository_git_lock(55):
            raise AssertionError("lock body must not run")

    assert len(client.set_calls) == 3
    assert sleeps
