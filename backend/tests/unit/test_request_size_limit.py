from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from starlette.types import Message, Receive, Scope, Send

from blade_backend.observability.security import (
    ATTACHMENT_UPLOAD_PATH,
    RequestSizeLimitMiddleware,
    request_body_limit,
)
from blade_backend.settings import get_settings


class _SpyApp:
    def __init__(self) -> None:
        self.called = False
        self.body = b""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        del scope
        self.called = True
        while True:
            message = await receive()
            self.body += bytes(message.get("body", b""))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


class _Recorder:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    async def send(self, message: Message) -> None:
        self.messages.append(message)

    @property
    def status(self) -> int:
        for message in self.messages:
            if message["type"] == "http.response.start":
                return int(message["status"])
        raise AssertionError("no response was sent")


def _scope(path: str, headers: list[tuple[bytes, bytes]]) -> Scope:
    return {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": headers,
        "client": ("198.51.100.7", 40000),
        "server": ("testserver", 80),
    }


async def _unread_body() -> Message:
    raise AssertionError("the request body was read before the size check")


@pytest.mark.unit
async def test_an_oversized_declared_body_is_rejected_before_the_body_is_read() -> None:
    spy = _SpyApp()
    middleware = RequestSizeLimitMiddleware(spy)
    limit = request_body_limit("/api/v1/scan/email")
    recorder = _Recorder()

    await middleware(
        _scope("/api/v1/scan/email", [(b"content-length", str(limit + 1).encode("ascii"))]),
        _unread_body,
        recorder.send,
    )

    assert recorder.status == 413
    assert spy.called is False


@pytest.mark.unit
async def test_a_chunked_body_is_cut_off_once_it_passes_the_limit() -> None:
    spy = _SpyApp()
    middleware = RequestSizeLimitMiddleware(spy)
    limit = request_body_limit("/api/v1/scan/email")
    chunk = b"x" * 65536
    sent = 0

    async def receive() -> Message:
        nonlocal sent
        sent += len(chunk)
        return {"type": "http.request", "body": chunk, "more_body": True}

    recorder = _Recorder()
    await middleware(_scope("/api/v1/scan/email", []), receive, recorder.send)

    assert recorder.status == 413
    assert sent <= limit + len(chunk)


@pytest.mark.unit
async def test_a_body_within_the_limit_reaches_the_application() -> None:
    spy = _SpyApp()
    middleware = RequestSizeLimitMiddleware(spy)
    recorder = _Recorder()

    async def receive() -> Message:
        return {"type": "http.request", "body": b"hello", "more_body": False}

    await middleware(
        _scope("/api/v1/scan/email", [(b"content-length", b"5")]),
        receive,
        recorder.send,
    )

    assert recorder.status == 200
    assert spy.body == b"hello"


@pytest.mark.unit
def test_the_attachment_upload_path_gets_its_own_larger_limit() -> None:
    settings = get_settings()

    assert request_body_limit(ATTACHMENT_UPLOAD_PATH) > settings.scan_max_attachment_bytes
    assert request_body_limit("/api/v1/scan/email") == settings.api_max_request_bytes
    assert request_body_limit(ATTACHMENT_UPLOAD_PATH) > request_body_limit("/api/v1/scan/email")


@pytest.mark.unit
async def test_an_oversized_scan_request_is_refused_before_authentication(
    client: AsyncClient,
) -> None:
    oversized = "x" * (get_settings().api_max_request_bytes + 1024)
    response = await client.post(
        "/api/v1/scan/email",
        content=json.dumps({"client_scan_id": "abcd", "body": oversized}),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "request body too large"


@pytest.mark.unit
async def test_a_streamed_body_without_a_content_length_is_still_refused(
    client: AsyncClient,
) -> None:
    limit = get_settings().api_max_request_bytes
    streamed = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal streamed
        chunk = b"x" * (512 * 1024)
        while streamed < limit * 2:
            streamed += len(chunk)
            yield chunk

    response = await client.post(
        "/api/v1/scan/email",
        content=body(),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert streamed < limit * 2, "the body was drained instead of being cut off"
