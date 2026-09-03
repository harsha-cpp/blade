from __future__ import annotations

import hashlib
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from typing import cast

from redis.asyncio import Redis
from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from blade_backend.settings import get_settings

_UNKNOWN_CLIENT = "unknown"

ATTACHMENT_UPLOAD_PATH = "/api/v1/scan/attachment"
MULTIPART_ENVELOPE_BYTES = 64 * 1024


def request_body_limit(path: str) -> int:
    settings = get_settings()
    if path.rstrip("/") == ATTACHMENT_UPLOAD_PATH:
        return settings.scan_max_attachment_bytes + MULTIPART_ENVELOPE_BYTES
    return settings.api_max_request_bytes


def _declared_body_size(scope: Scope) -> int | None:
    raw = Headers(scope=scope).get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _too_large_response(limit: int) -> JSONResponse:
    return JSONResponse({"detail": "request body too large", "max_bytes": limit}, status_code=413)


class _BodySizeGuard:
    def __init__(self, receive: Receive, limit: int) -> None:
        self._receive = receive
        self._limit = limit
        self._received = 0
        self.exceeded = False

    async def receive(self) -> Message:
        if self.exceeded:
            return {"type": "http.disconnect"}
        message = await self._receive()
        if message["type"] != "http.request":
            return message
        self._received += len(cast(bytes, message.get("body", b"")))
        if self._received <= self._limit:
            return message
        self.exceeded = True
        return {"type": "http.disconnect"}


class _RejectingSend:
    def __init__(self, send: Send, guard: _BodySizeGuard, rejection: JSONResponse) -> None:
        self._send = send
        self._guard = guard
        self._rejection = rejection
        self._rejection_sent = False
        self.started = False

    async def send(self, message: Message) -> None:
        if not self._guard.exceeded:
            self.started = self.started or message["type"] == "http.response.start"
            await self._send(message)
            return
        if message["type"] == "http.response.start":
            self.started = True
            await self._send(
                {
                    "type": "http.response.start",
                    "status": self._rejection.status_code,
                    "headers": self._rejection.raw_headers,
                }
            )
            return
        if message["type"] == "http.response.body" and not self._rejection_sent:
            self._rejection_sent = True
            await self._send({"type": "http.response.body", "body": self._rejection.body})


class RequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = request_body_limit(str(scope.get("path", "")))
        rejection = _too_large_response(limit)
        declared = _declared_body_size(scope)
        if declared is not None and declared > limit:
            await rejection(scope, receive, send)
            return

        guard = _BodySizeGuard(receive, limit)
        response = _RejectingSend(send, guard, rejection)
        try:
            await self.app(scope, guard.receive, response.send)
        except Exception:
            if response.started or not guard.exceeded:
                raise
            await rejection(scope, receive, send)


def resolve_client_ip(request: Request, trusted_proxy_count: int) -> str:
    fallback = request.client.host if request.client else _UNKNOWN_CLIENT
    if trusted_proxy_count <= 0:
        return fallback
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return fallback
    parts = [part.strip() for part in forwarded.split(",") if part.strip()]
    if len(parts) < trusted_proxy_count:
        return fallback
    return parts[-trusted_proxy_count]


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["content-security-policy"] = "frame-ancestors 'none'"
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["referrer-policy"] = "no-referrer"
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        limit: int | None = None,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app)
        self._limit_override = limit
        self._window_seconds = window_seconds
        self._buckets: OrderedDict[str, deque[float]] = OrderedDict()
        self._redis: Redis | None = None
        self._redis_checked = False

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if not path.startswith("/api/v1/") or self._is_exempt(path):
            return await call_next(request)
        if await self._over_limit(self._key(request)):
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"retry-after": str(self._window_seconds)},
            )
        return await call_next(request)

    def _is_exempt(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in get_settings().api_rate_limit_exempt_paths)

    @property
    def _limit(self) -> int:
        if self._limit_override is not None:
            return self._limit_override
        return get_settings().api_rate_limit_per_minute

    async def _over_limit(self, key: str) -> bool:
        redis = await self._get_redis()
        if redis is not None:
            try:
                count = await redis.incr(f"apirl:{key}")
                if count == 1:
                    await redis.expire(f"apirl:{key}", self._window_seconds)
            except Exception:
                self._redis = None
            else:
                return count > self._limit
        return self._over_limit_memory(key)

    async def _get_redis(self) -> Redis | None:
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        try:
            redis = Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
                get_settings().redis_url,
                socket_connect_timeout=0.05,
                socket_timeout=0.05,
                decode_responses=True,
            )
            ping = cast(
                Callable[[], Awaitable[object]],
                redis.ping,  # pyright: ignore[reportUnknownMemberType]
            )
            await ping()
            self._redis = redis
        except Exception:
            self._redis = None
        return self._redis

    def _over_limit_memory(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._buckets.get(key)
        if bucket is None:
            self._evict_if_full()
            bucket = deque[float]()
            self._buckets[key] = bucket
        self._buckets.move_to_end(key)
        while bucket and now - bucket[0] > self._window_seconds:
            bucket.popleft()
        if len(bucket) >= self._limit:
            return True
        bucket.append(now)
        return False

    def _evict_if_full(self) -> None:
        max_keys = get_settings().api_rate_limit_max_keys
        while len(self._buckets) >= max_keys:
            self._buckets.popitem(last=False)

    def _key(self, request: Request) -> str:
        authorization = request.headers.get("authorization")
        if authorization:
            digest = hashlib.sha256(authorization.encode("utf-8")).hexdigest()
            return f"auth:{digest}"
        device_token = request.headers.get("x-blade-device-token")
        if device_token:
            digest = hashlib.sha256(device_token.encode("utf-8")).hexdigest()
            return f"device:{digest}"
        client = resolve_client_ip(request, get_settings().trusted_proxy_count)
        return f"ip:{client}"
