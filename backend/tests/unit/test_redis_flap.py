# pyright: reportPrivateUsage=false

from __future__ import annotations

from uuid import uuid4

import pytest

from blade_backend.auth.rate_limit import MFA_MAX_ATTEMPTS, MfaChallengeGuard
from blade_backend.observability.redis_gate import RedisGate


class _FlakyRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.reachable = True

    async def incr(self, key: str) -> int:
        self._require_reachable()
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        del key, ttl_seconds
        self._require_reachable()
        return True

    async def get(self, key: str) -> str | None:
        self._require_reachable()
        value = self.values.get(key)
        return None if value is None else str(value)

    async def delete(self, key: str) -> int:
        self._require_reachable()
        self.values.pop(key, None)
        return 1

    def _require_reachable(self) -> None:
        if not self.reachable:
            raise ConnectionError("redis is unreachable")


class _StubGate:
    def __init__(self, redis: _FlakyRedis) -> None:
        self.redis = redis
        self.drops = 0
        self.dropped = False

    async def client(self) -> _FlakyRedis | None:
        return None if self.dropped else self.redis

    def drop(self) -> None:
        self.drops += 1
        self.dropped = True


@pytest.mark.unit
async def test_mfa_failures_are_mirrored_so_a_redis_flap_does_not_reset_a_lockout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = MfaChallengeGuard()
    redis = _FlakyRedis()
    gate = _StubGate(redis)
    monkeypatch.setattr(guard, "_redis", gate)
    member_id = uuid4()

    for _ in range(MFA_MAX_ATTEMPTS - 1):
        await guard.record_failure(member_id)
    assert await guard.is_locked_out(member_id) is False

    redis.reachable = False

    assert await guard.record_failure(member_id) == MFA_MAX_ATTEMPTS
    assert await guard.is_locked_out(member_id) is True
    assert gate.drops == 1


@pytest.mark.unit
async def test_a_cleared_lockout_is_cleared_in_the_mirror_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = MfaChallengeGuard()
    redis = _FlakyRedis()
    monkeypatch.setattr(guard, "_redis", _StubGate(redis))
    member_id = uuid4()

    for _ in range(MFA_MAX_ATTEMPTS):
        await guard.record_failure(member_id)
    await guard.clear_failures(member_id)

    redis.reachable = False

    assert await guard.is_locked_out(member_id) is False


@pytest.mark.unit
def test_losing_redis_is_logged_once_per_backoff_window(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gate = RedisGate()

    gate.drop()

    logged = capsys.readouterr().out
    assert "redis dropped out of the request path" in logged
    assert "per worker" in logged
