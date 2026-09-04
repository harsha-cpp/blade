# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.auth.tokens import (
    DEVICE_TOKEN_PREFIX,
    EMAIL_VERIFY_PREFIX,
    PASSWORD_RESET_PREFIX,
    issue_token,
)
from blade_backend.db.models import (
    AccessRequest,
    DeviceToken,
    EmailVerificationToken,
    Organization,
    OrgMember,
    PasswordResetToken,
    RefreshToken,
)
from blade_backend.settings import get_settings
from blade_backend.tasks import maintenance_tasks
from blade_backend.utils.uuid import uuid7
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]


async def _org_with_member(session: AsyncSession) -> tuple[Organization, OrgMember]:
    suffix = uuid.uuid4().hex[:10]
    org = Organization(
        name=f"Purge Hospital {suffix}",
        slug=f"purge-hospital-{suffix}",
        org_code=f"BLD-PURGE{suffix.upper()}",
    )
    session.add(org)
    await session.flush()
    member = OrgMember(
        org_id=org.id,
        email=f"owner-{suffix}@purgehospital.in",
        role="owner",
        status="active",
    )
    session.add(member)
    await session.flush()
    return org, member


async def test_the_purge_removes_dead_credentials_and_keeps_live_ones(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOKEN_RETENTION_DAYS", "30")
    monkeypatch.setenv("ACCESS_REQUEST_RETENTION_DAYS", "90")
    get_settings.cache_clear()
    maintenance_tasks._sync_engine.cache_clear()

    now = datetime.now(UTC)
    ancient = now - timedelta(days=400)
    org, member = await _org_with_member(db_session)

    dead_refresh = RefreshToken(
        id=uuid7(),
        member_id=member.id,
        family_id=uuid7(),
        token_hash=b"dead",
        expires_at=ancient,
        created_at=ancient,
    )
    live_refresh = RefreshToken(
        id=uuid7(),
        member_id=member.id,
        family_id=uuid7(),
        token_hash=b"live",
        expires_at=now + timedelta(days=30),
        created_at=now,
    )
    reset = await asyncio.to_thread(issue_token, PASSWORD_RESET_PREFIX, ttl=timedelta(seconds=300))
    dead_reset = PasswordResetToken(
        id=reset.id,
        member_id=member.id,
        token_hash=reset.token_hash,
        expires_at=ancient,
        created_at=ancient,
    )
    verification = await asyncio.to_thread(
        issue_token, EMAIL_VERIFY_PREFIX, ttl=timedelta(hours=24)
    )
    dead_verification = EmailVerificationToken(
        id=verification.id,
        member_id=member.id,
        token_hash=verification.token_hash,
        expires_at=ancient,
        created_at=ancient,
    )
    device = await asyncio.to_thread(issue_token, DEVICE_TOKEN_PREFIX, ttl=timedelta(days=365))
    revoked_device = DeviceToken(
        id=device.id,
        org_id=org.id,
        member_id=member.id,
        label="retired laptop",
        token_hash=device.token_hash,
        expires_at=now + timedelta(days=365),
        revoked_at=ancient,
        created_at=ancient,
    )
    stale_lead = AccessRequest(
        id=uuid7(),
        created_at=ancient,
        organization="Old Enquiry",
        name="Someone",
        email=f"stale-{uuid.uuid4().hex[:10]}@enquiry.example",
    )
    fresh_lead = AccessRequest(
        id=uuid7(),
        created_at=now,
        organization="New Enquiry",
        name="Someone Else",
        email=f"fresh-{uuid.uuid4().hex[:10]}@enquiry.example",
    )
    db_session.add_all(
        [
            dead_refresh,
            live_refresh,
            dead_reset,
            dead_verification,
            revoked_device,
            stale_lead,
            fresh_lead,
        ]
    )
    await db_session.commit()

    purged = await asyncio.to_thread(maintenance_tasks.purge_expired_tokens)
    db_session.expunge_all()

    assert purged["refresh_tokens"] >= 1
    assert purged["password_reset_tokens"] >= 1
    assert purged["email_verification_tokens"] >= 1
    assert purged["device_tokens"] >= 1
    assert purged["access_requests"] >= 1

    assert await db_session.get(RefreshToken, dead_refresh.id) is None
    assert await db_session.get(PasswordResetToken, dead_reset.id) is None
    assert await db_session.get(EmailVerificationToken, dead_verification.id) is None
    assert await db_session.get(DeviceToken, revoked_device.id) is None
    assert await db_session.get(AccessRequest, stale_lead.id) is None

    surviving = await db_session.scalar(
        select(RefreshToken.id).where(RefreshToken.id == live_refresh.id)
    )
    assert surviving == live_refresh.id
    assert await db_session.get(AccessRequest, fresh_lead.id) is not None


async def test_access_requests_are_kept_forever_when_retention_is_zero(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACCESS_REQUEST_RETENTION_DAYS", "0")
    get_settings.cache_clear()
    maintenance_tasks._sync_engine.cache_clear()

    ancient = datetime.now(UTC) - timedelta(days=400)
    lead = AccessRequest(
        id=uuid7(),
        created_at=ancient,
        organization="Old Enquiry",
        name="Someone",
        email=f"keep-{uuid.uuid4().hex[:10]}@enquiry.example",
    )
    db_session.add(lead)
    await db_session.commit()

    purged = await asyncio.to_thread(maintenance_tasks.purge_expired_tokens)
    db_session.expunge_all()

    assert purged["access_requests"] == 0
    assert await db_session.get(AccessRequest, lead.id) is not None
