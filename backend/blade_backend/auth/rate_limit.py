from __future__ import annotations

import hashlib
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from uuid import UUID

from redis.asyncio import Redis
from starlette.requests import Request

from blade_backend.auth.mfa import CHALLENGE_TTL_SECONDS
from blade_backend.observability.redis_gate import RedisGate, hit_counter
from blade_backend.observability.security import resolve_client_ip
from blade_backend.settings import Settings, get_settings

MAX_TRACKED_KEYS = 20_000
MFA_MAX_ATTEMPTS = 5
ACCESS_REQUEST_PER_HOUR = 5
ACCESS_REQUEST_PER_DAY = 20

FAILED_LOGIN_AUDIT_WINDOW_SECONDS = 60

_MINUTE_SECONDS = 60
_HOUR_SECONDS = 3600
_DAY_SECONDS = 86_400
_IP_BUDGET_FACTOR = 20
_EMAIL_BUDGET_FACTOR = 3


@dataclass(frozen=True, slots=True)
class LoginLimitResult:
    allowed: bool
    retry_after_seconds: int = 0
    audit_due: bool = False


@dataclass(frozen=True, slots=True)
class WindowLimitResult:
    allowed: bool
    retry_after_seconds: int = 0


@dataclass(frozen=True, slots=True)
class _Budget:
    key: str
    factor: int


@dataclass(slots=True)
class _Counter:
    expires_at: float
    hits: int


class _BoundedBuckets:
    def __init__(self, max_keys: int = MAX_TRACKED_KEYS) -> None:
        self._buckets: OrderedDict[str, deque[float]] = OrderedDict()
        self._max_keys = max_keys

    def bucket(self, key: str) -> deque[float]:
        found = self._buckets.get(key)
        if found is None:
            found = deque[float]()
            self._buckets[key] = found
        self._buckets.move_to_end(key)
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)
        return found

    def tracked_keys(self) -> int:
        return len(self._buckets)


class _BoundedCounters:
    def __init__(self, max_keys: int = MAX_TRACKED_KEYS) -> None:
        self._counters: OrderedDict[str, _Counter] = OrderedDict()
        self._max_keys = max_keys

    def increment(self, key: str, ttl_seconds: int) -> int:
        counter = self._live(key)
        if counter is None:
            counter = _Counter(expires_at=time.monotonic() + ttl_seconds, hits=0)
            self._counters[key] = counter
        counter.hits += 1
        self._counters.move_to_end(key)
        while len(self._counters) > self._max_keys:
            self._counters.popitem(last=False)
        return counter.hits

    def hits(self, key: str) -> int:
        counter = self._live(key)
        return counter.hits if counter is not None else 0

    def mirror(self, key: str, hits: int, ttl_seconds: int) -> int:
        if hits <= 0:
            self._counters.pop(key, None)
            return 0
        counter = self._live(key)
        if counter is None:
            self._counters[key] = _Counter(expires_at=time.monotonic() + ttl_seconds, hits=hits)
        else:
            counter.hits = hits
        self._counters.move_to_end(key)
        while len(self._counters) > self._max_keys:
            self._counters.popitem(last=False)
        return hits

    def discard(self, key: str) -> None:
        self._counters.pop(key, None)

    def tracked_keys(self) -> int:
        return len(self._counters)

    def _live(self, key: str) -> _Counter | None:
        counter = self._counters.get(key)
        if counter is None:
            return None
        if time.monotonic() >= counter.expires_at:
            self._counters.pop(key, None)
            return None
        return counter


class LoginRateLimiter:
    def __init__(self) -> None:
        self._buckets = _BoundedBuckets()
        self._redis = RedisGate()

    async def check(self, request: Request, email: str) -> LoginLimitResult:
        settings = get_settings()
        client = resolve_client_ip(request, settings.trusted_proxy_count)
        identity = email.strip().lower()
        budgets = (
            _Budget(_digest(f"pair:{client}:{identity}"), 1),
            _Budget(_digest(f"ip:{client}"), _IP_BUDGET_FACTOR),
            _Budget(_digest(f"email:{identity}"), _EMAIL_BUDGET_FACTOR),
        )
        redis = await self._redis.client()
        if redis is not None:
            try:
                return await self._check_redis(redis, budgets, settings)
            except Exception:
                self._redis.drop()
        return self._check_memory(budgets, settings)

    async def _check_redis(
        self,
        redis: Redis,
        budgets: tuple[_Budget, ...],
        settings: Settings,
    ) -> LoginLimitResult:
        for budget in budgets:
            minute = await hit_counter(redis, f"login:{budget.key}:m", _MINUTE_SECONDS)
            if minute > settings.login_rate_limit_per_minute * budget.factor:
                return await self._blocked_redis(redis, budget, "m")
            hour = await hit_counter(redis, f"login:{budget.key}:h", _HOUR_SECONDS)
            if hour > settings.login_rate_limit_per_hour * budget.factor:
                return await self._blocked_redis(redis, budget, "h")
        return LoginLimitResult(True)

    async def _blocked_redis(self, redis: Redis, budget: _Budget, suffix: str) -> LoginLimitResult:
        ttl = max(int(await redis.ttl(f"login:{budget.key}:{suffix}")), 1)
        claimed = await redis.set(f"loginlock:{budget.key}:{suffix}", "1", ex=ttl, nx=True)
        return LoginLimitResult(False, ttl, audit_due=bool(claimed))

    def _check_memory(self, budgets: tuple[_Budget, ...], settings: Settings) -> LoginLimitResult:
        now = time.monotonic()
        for budget in budgets:
            minute_bucket = self._buckets.bucket(f"{budget.key}:m")
            _prune(minute_bucket, now, _MINUTE_SECONDS)
            if len(minute_bucket) >= settings.login_rate_limit_per_minute * budget.factor:
                return self._blocked_memory(budget, minute_bucket, now, _MINUTE_SECONDS, "m")
            hour_bucket = self._buckets.bucket(f"{budget.key}:h")
            _prune(hour_bucket, now, _HOUR_SECONDS)
            if len(hour_bucket) >= settings.login_rate_limit_per_hour * budget.factor:
                return self._blocked_memory(budget, hour_bucket, now, _HOUR_SECONDS, "h")
        for budget in budgets:
            self._buckets.bucket(f"{budget.key}:m").append(now)
            self._buckets.bucket(f"{budget.key}:h").append(now)
        return LoginLimitResult(True)

    def _blocked_memory(
        self,
        budget: _Budget,
        bucket: deque[float],
        now: float,
        window_seconds: int,
        suffix: str,
    ) -> LoginLimitResult:
        lockout = self._buckets.bucket(f"lock:{budget.key}:{suffix}")
        _prune(lockout, now, window_seconds)
        audit_due = not lockout
        if audit_due:
            lockout.append(now)
        return LoginLimitResult(False, _retry_after(bucket, now, window_seconds), audit_due)

    async def claim_failure_audit(self, request: Request, email: str) -> bool:
        settings = get_settings()
        client = resolve_client_ip(request, settings.trusted_proxy_count)
        key = _digest(f"pair:{client}:{email.strip().lower()}")
        redis = await self._redis.client()
        if redis is not None:
            try:
                claimed = await redis.set(
                    f"loginfail:{key}",
                    "1",
                    ex=FAILED_LOGIN_AUDIT_WINDOW_SECONDS,
                    nx=True,
                )
            except Exception:
                self._redis.drop()
            else:
                return bool(claimed)
        return self._claim_memory(f"loginfail:{key}")

    def _claim_memory(self, key: str) -> bool:
        now = time.monotonic()
        claims = self._buckets.bucket(key)
        _prune(claims, now, FAILED_LOGIN_AUDIT_WINDOW_SECONDS)
        if claims:
            return False
        claims.append(now)
        return True

    def tracked_keys(self) -> int:
        return self._buckets.tracked_keys()


class MfaChallengeGuard:
    def __init__(self) -> None:
        self._redis = RedisGate()
        self._counters = _BoundedCounters()

    async def is_locked_out(self, member_id: UUID) -> bool:
        return await self._hits(_failure_key(member_id)) >= MFA_MAX_ATTEMPTS

    async def record_failure(self, member_id: UUID) -> int:
        return await self._increment(_failure_key(member_id))

    async def clear_failures(self, member_id: UUID) -> None:
        await self._discard(_failure_key(member_id))

    async def is_spent(self, challenge_id: str) -> bool:
        return await self._hits(_challenge_key(challenge_id)) > 0

    async def spend(self, challenge_id: str) -> None:
        await self._increment(_challenge_key(challenge_id))

    async def _increment(self, key: str) -> int:
        redis = await self._redis.client()
        if redis is not None:
            try:
                hits = await hit_counter(redis, key, CHALLENGE_TTL_SECONDS)
            except Exception:
                self._redis.drop()
            else:
                return self._counters.mirror(key, hits, CHALLENGE_TTL_SECONDS)
        return self._counters.increment(key, CHALLENGE_TTL_SECONDS)

    async def _hits(self, key: str) -> int:
        redis = await self._redis.client()
        if redis is not None:
            try:
                stored = await redis.get(key)
            except Exception:
                self._redis.drop()
            else:
                return self._counters.mirror(
                    key,
                    int(stored) if stored is not None else 0,
                    CHALLENGE_TTL_SECONDS,
                )
        return self._counters.hits(key)

    async def _discard(self, key: str) -> None:
        redis = await self._redis.client()
        if redis is not None:
            try:
                await redis.delete(key)
            except Exception:
                self._redis.drop()
        self._counters.discard(key)

    def tracked_keys(self) -> int:
        return self._counters.tracked_keys()


def _failure_key(member_id: UUID) -> str:
    return f"mfafail:{member_id}"


def _challenge_key(challenge_id: str) -> str:
    return f"mfachallenge:{challenge_id}"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _prune(bucket: deque[float], now: float, window_seconds: int) -> None:
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()


def _retry_after(bucket: deque[float], now: float, window_seconds: int) -> int:
    if not bucket:
        return window_seconds
    return max(1, int(window_seconds - (now - bucket[0])))


class AccessRequestLimiter:
    def __init__(self) -> None:
        self._redis = RedisGate()
        self._buckets = _BoundedBuckets()

    async def check(self, request: Request) -> WindowLimitResult:
        client = resolve_client_ip(request, get_settings().trusted_proxy_count)
        key = _digest(f"accessrequest:{client}")
        redis = await self._redis.client()
        if redis is not None:
            try:
                return await self._check_redis(redis, key)
            except Exception:
                self._redis.drop()
        return self._check_memory(key)

    async def _check_redis(self, redis: Redis, key: str) -> WindowLimitResult:
        hourly = await hit_counter(redis, f"accessreq:{key}:h", _HOUR_SECONDS)
        if hourly > ACCESS_REQUEST_PER_HOUR:
            return WindowLimitResult(False, max(int(await redis.ttl(f"accessreq:{key}:h")), 1))
        daily = await hit_counter(redis, f"accessreq:{key}:d", _DAY_SECONDS)
        if daily > ACCESS_REQUEST_PER_DAY:
            return WindowLimitResult(False, max(int(await redis.ttl(f"accessreq:{key}:d")), 1))
        return WindowLimitResult(True)

    def _check_memory(self, key: str) -> WindowLimitResult:
        now = time.monotonic()
        hourly = self._buckets.bucket(f"{key}:h")
        _prune(hourly, now, _HOUR_SECONDS)
        if len(hourly) >= ACCESS_REQUEST_PER_HOUR:
            return WindowLimitResult(False, _retry_after(hourly, now, _HOUR_SECONDS))
        daily = self._buckets.bucket(f"{key}:d")
        _prune(daily, now, _DAY_SECONDS)
        if len(daily) >= ACCESS_REQUEST_PER_DAY:
            return WindowLimitResult(False, _retry_after(daily, now, _DAY_SECONDS))
        hourly.append(now)
        daily.append(now)
        return WindowLimitResult(True)

    def tracked_keys(self) -> int:
        return self._buckets.tracked_keys()


login_rate_limiter = LoginRateLimiter()
mfa_challenge_guard = MfaChallengeGuard()
access_request_limiter = AccessRequestLimiter()
