from __future__ import annotations

import time
from collections import OrderedDict, deque
from collections.abc import Sequence
from typing import cast

from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from blade_backend.auth.jwt import TokenError, decode_access_token
from blade_backend.observability.redis_gate import RedisGate, hit_counter
from blade_backend.settings import get_settings

_UNKNOWN_CLIENT = "unknown"

ATTACHMENT_UPLOAD_PATH = "/api/v1/scan/attachment"
MULTIPART_ENVELOPE_BYTES = 64 * 1024

LANDING_CORS_PATHS = (
    "/api/v1/public/access-requests",
    "/api/v1/public/signup-status",
)

NO_STORE_PATH_PREFIXES = (
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/signup",
    "/api/v1/auth/switch-org",
    "/api/v1/auth/mfa",
    "/api/v1/members/accept-invite",
    "/api/v1/devices/enroll",
)


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


def carries_credentials(path: str) -> bool:
    return path.rstrip("/").startswith(NO_STORE_PATH_PREFIXES)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["content-security-policy"] = "frame-ancestors 'none'"
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["referrer-policy"] = "no-referrer"
        if carries_credentials(request.url.path):
            response.headers["cache-control"] = "no-store"
        return response


def deduplicate_vary(headers: MutableHeaders) -> None:
    raw = headers.get("vary")
    if raw is None:
        return
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in raw.split(","):
        value = candidate.strip()
        if value and value.lower() not in seen:
            seen.add(value.lower())
            unique.append(value)
    headers["vary"] = ", ".join(unique)


class _UncredentialedCorsSend:
    def __init__(self, send: Send, *, strip_credentials: bool) -> None:
        self._send = send
        self._strip_credentials = strip_credentials

    async def send(self, message: Message) -> None:
        if message["type"] != "http.response.start":
            await self._send(message)
            return
        message.setdefault("headers", [])
        headers = MutableHeaders(scope=message)
        if self._strip_credentials:
            del headers["access-control-allow-credentials"]
        deduplicate_vary(headers)
        await self._send(message)


class PathScopedCORSMiddleware(CORSMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        paths: Sequence[str],
        allow_origins: Sequence[str] = (),
        allow_methods: Sequence[str] = ("GET",),
        allow_headers: Sequence[str] = (),
        allow_credentials: bool = False,
        max_age: int = 600,
    ) -> None:
        super().__init__(
            app,
            allow_origins=allow_origins,
            allow_methods=allow_methods,
            allow_headers=allow_headers,
            allow_credentials=allow_credentials,
            max_age=max_age,
        )
        self._paths = tuple(paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or str(scope.get("path", "")).rstrip("/") not in self._paths:
            await self.app(scope, receive, send)
            return
        origin = Headers(scope=scope).get("origin")
        scoped = origin is not None and self.is_allowed_origin(origin=origin)
        guard = _UncredentialedCorsSend(
            send,
            strip_credentials=scoped and not self.allow_credentials,
        )
        await super().__call__(scope, receive, guard.send)


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
        self._redis = RedisGate()

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
        redis = await self._redis.client()
        if redis is not None:
            try:
                count = await hit_counter(redis, f"apirl:{key}", self._window_seconds)
            except Exception:
                self._redis.drop()
            else:
                return count > self._limit
        return self._over_limit_memory(key)

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
        member_id = verified_member_subject(request.headers.get("authorization"))
        if member_id is not None:
            return f"member:{member_id}"
        client = resolve_client_ip(request, get_settings().trusted_proxy_count)
        return f"ip:{client}"


def verified_member_subject(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _separator, token = authorization.partition(" ")
    candidate = token.strip()
    if scheme.lower() != "bearer" or not candidate:
        return None
    try:
        return decode_access_token(candidate).sub
    except TokenError:
        return None
