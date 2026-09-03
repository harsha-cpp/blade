from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from argon2.low_level import Type
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.auth.tokens import (
    DEVICE_TOKEN_PREFIX,
    is_legacy_token_hash,
    issue_token,
    parse_token,
    verify_token_secret,
)
from blade_backend.db.models import DeviceToken
from blade_backend.deps import current_device


class _FakeSession:
    def __init__(self, device: DeviceToken | None) -> None:
        self.device = device
        self.commits = 0

    async def get(self, model: object, _id: object) -> object | None:
        return self.device if model is DeviceToken else None

    async def commit(self) -> None:
        self.commits += 1


def _as_session(session: _FakeSession) -> AsyncSession:
    return cast(AsyncSession, cast(Any, session))


def _device(
    *,
    revoked_at: datetime | None = None,
    expires_in: timedelta = timedelta(days=365),
    last_seen_at: datetime | None = None,
) -> tuple[str, DeviceToken]:
    issued = issue_token(DEVICE_TOKEN_PREFIX, ttl=expires_in)
    device = DeviceToken(
        id=issued.id,
        org_id=uuid4(),
        member_id=uuid4(),
        member_email="dr@hospital.in",
        label="Reception laptop",
        token_hash=issued.token_hash,
        expires_at=datetime.now(UTC) + expires_in,
        revoked_at=revoked_at,
        last_seen_at=last_seen_at,
    )
    return issued.raw_token, device


@pytest.mark.unit
async def test_valid_device_token_resolves_to_its_org_and_member() -> None:
    raw_token, device = _device()
    session = _FakeSession(device)

    principal = await current_device(_as_session(session), raw_token)

    assert principal.device_id == device.id
    assert principal.org_id == device.org_id
    assert principal.member_id == device.member_id
    assert principal.email == "dr@hospital.in"


@pytest.mark.unit
async def test_revoked_device_is_refused_without_touching_other_installs() -> None:
    raw_token, device = _device(revoked_at=datetime.now(UTC))

    with pytest.raises(HTTPException) as exc_info:
        await current_device(_as_session(_FakeSession(device)), raw_token)

    assert exc_info.value.status_code == 401


@pytest.mark.unit
async def test_expired_device_token_is_refused() -> None:
    raw_token, device = _device(expires_in=timedelta(days=-1))

    with pytest.raises(HTTPException) as exc_info:
        await current_device(_as_session(_FakeSession(device)), raw_token)

    assert exc_info.value.status_code == 401


@pytest.mark.unit
async def test_token_secret_must_match_the_stored_hash() -> None:
    raw_token, device = _device()
    _other_raw, other_device = _device()
    device.token_hash = other_device.token_hash

    with pytest.raises(HTTPException) as exc_info:
        await current_device(_as_session(_FakeSession(device)), raw_token)

    assert exc_info.value.status_code == 401


@pytest.mark.unit
@pytest.mark.parametrize("header", [None, "", "Bearer abc", "bladereset_x.y"])
async def test_non_device_credentials_are_refused(header: str | None) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await current_device(_as_session(_FakeSession(None)), header)

    assert exc_info.value.status_code == 401


@pytest.mark.unit
async def test_last_seen_is_not_rewritten_on_every_scan() -> None:
    raw_token, device = _device(last_seen_at=datetime.now(UTC))
    session = _FakeSession(device)

    await current_device(_as_session(session), raw_token)

    assert session.commits == 0


@pytest.mark.unit
async def test_stale_last_seen_is_refreshed() -> None:
    raw_token, device = _device(last_seen_at=datetime.now(UTC) - timedelta(hours=2))
    session = _FakeSession(device)

    await current_device(_as_session(session), raw_token)

    assert session.commits == 1


def _argon2_hash(secret: str) -> bytes:
    return PasswordHasher(type=Type.ID).hash(secret).encode("utf-8")


@pytest.mark.unit
async def test_a_device_stored_as_argon2_still_authenticates_and_is_upgraded() -> None:
    raw_token, device = _device(last_seen_at=datetime.now(UTC))
    _device_id, secret = parse_token(DEVICE_TOKEN_PREFIX, raw_token)
    device.token_hash = _argon2_hash(secret)
    session = _FakeSession(device)

    principal = await current_device(_as_session(session), raw_token)

    assert principal.org_id == device.org_id
    assert session.commits == 1
    assert is_legacy_token_hash(device.token_hash) is False
    assert verify_token_secret(secret, device.token_hash) is True


@pytest.mark.unit
async def test_a_revoked_device_stored_as_argon2_is_refused_without_being_upgraded() -> None:
    raw_token, device = _device(revoked_at=datetime.now(UTC))
    _device_id, secret = parse_token(DEVICE_TOKEN_PREFIX, raw_token)
    legacy_hash = _argon2_hash(secret)
    device.token_hash = legacy_hash
    session = _FakeSession(device)

    with pytest.raises(HTTPException) as exc_info:
        await current_device(_as_session(session), raw_token)

    assert exc_info.value.status_code == 401
    assert device.token_hash == legacy_hash
    assert session.commits == 0


@pytest.mark.unit
async def test_a_wrong_secret_against_an_argon2_device_is_refused() -> None:
    raw_token, device = _device()
    _device_id, secret = parse_token(DEVICE_TOKEN_PREFIX, raw_token)
    device.token_hash = _argon2_hash(secret + "x")

    with pytest.raises(HTTPException) as exc_info:
        await current_device(_as_session(_FakeSession(device)), raw_token)

    assert exc_info.value.status_code == 401
