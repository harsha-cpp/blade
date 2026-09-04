from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.db.models import ApprovedDomain, Organization

RECIPIENT_ALLOWED_CLASSES = frozenset({"internal", "partner"})
RECIPIENT_DIRECTIONS = frozenset({"recipient", "both"})


class PublicDomain(BaseModel):
    domain: str
    direction: str
    classification: str


class OrgConfig(BaseModel):
    fail_open: bool = False
    domains: list[PublicDomain]
    blocked_domains: list[PublicDomain]


def _public_domain(domain: ApprovedDomain) -> PublicDomain:
    return PublicDomain(
        domain=domain.domain,
        direction=domain.direction,
        classification=domain.classification,
    )


async def build_org_config(session: AsyncSession, org: Organization) -> OrgConfig:
    domains = (
        await session.scalars(
            select(ApprovedDomain)
            .where(ApprovedDomain.org_id == org.id)
            .order_by(ApprovedDomain.domain.asc(), ApprovedDomain.direction.asc())
        )
    ).all()
    recipient_allow = [
        domain
        for domain in domains
        if domain.classification in RECIPIENT_ALLOWED_CLASSES
        and domain.direction in RECIPIENT_DIRECTIONS
    ]
    blocked = [domain for domain in domains if domain.classification == "blocked"]
    settings_blob = org.settings or {}

    return OrgConfig(
        fail_open=bool(settings_blob.get("fail_open", False)),
        domains=[_public_domain(domain) for domain in recipient_allow],
        blocked_domains=[_public_domain(domain) for domain in blocked],
    )
