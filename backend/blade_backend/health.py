from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from blade_backend.db.session import check_database
from blade_backend.settings import Settings

MALFORMED_REDIS_URL_DETAIL = "invalid_url"


@dataclass(frozen=True, slots=True)
class ComponentStatus:
    name: str
    ok: bool
    detail: str
    required: bool = True

    def as_dict(self) -> dict[str, str | bool]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class ReadinessStatus:
    ready: bool
    components: tuple[ComponentStatus, ...]

    @property
    def degraded(self) -> bool:
        return any(not component.ok for component in self.components)

    def as_dict(self) -> dict[str, str | bool | list[dict[str, str | bool]]]:
        return {
            "status": "ready" if self.ready else "not_ready",
            "degraded": self.degraded,
            "components": [component.as_dict() for component in self.components],
        }


ReadinessProbe = Callable[[], Awaitable[ReadinessStatus]]


def build_readiness_probe(settings: Settings) -> ReadinessProbe:
    async def probe() -> ReadinessStatus:
        database, redis = await asyncio.gather(
            database_status(settings.readiness_timeout_seconds),
            redis_status(settings.redis_url, settings.readiness_timeout_seconds),
        )
        components = (database, redis)
        return ReadinessStatus(
            ready=all(component.ok for component in components if component.required),
            components=components,
        )

    return probe


async def database_status(timeout: float = 5.0) -> ComponentStatus:
    try:
        ok = await asyncio.wait_for(check_database(), timeout=timeout)
    except TimeoutError:
        return ComponentStatus(name="database", ok=False, detail="timeout")
    except Exception as exc:
        return ComponentStatus(name="database", ok=False, detail=type(exc).__name__)
    return ComponentStatus(name="database", ok=ok, detail="ok" if ok else "unexpected_response")


async def redis_status(redis_url: str, timeout: float = 5.0) -> ComponentStatus:
    try:
        client = Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
            redis_url,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
        )
    except (ValueError, RedisError):
        return ComponentStatus(name="redis", ok=False, detail=MALFORMED_REDIS_URL_DETAIL)

    ping = cast(
        Callable[[], Awaitable[object]],
        client.ping,  # pyright: ignore[reportUnknownMemberType]
    )
    close = cast(Callable[[], Awaitable[None]], client.aclose)
    try:
        await asyncio.wait_for(ping(), timeout=timeout)
    except TimeoutError:
        return _unreachable("timeout")
    except Exception as exc:
        return _unreachable(type(exc).__name__)
    finally:
        with suppress(Exception):
            await close()
    return ComponentStatus(name="redis", ok=True, detail="pong")


def _unreachable(detail: str) -> ComponentStatus:
    return ComponentStatus(name="redis", ok=False, detail=detail, required=False)
