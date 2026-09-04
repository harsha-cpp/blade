# pyright: reportPrivateUsage=false

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.db.models import ApprovedDomain, DomainClass
from blade_backend.scan.api import _classify_recipients

ORG_ID = uuid4()


class _ScalarRows:
    def __init__(self, rows: list[ApprovedDomain]) -> None:
        self._rows = rows

    def all(self) -> list[ApprovedDomain]:
        return self._rows


class _FakeSession:
    def __init__(self, domains: list[ApprovedDomain]) -> None:
        self.domains = domains

    async def scalars(self, _statement: object) -> _ScalarRows:
        return _ScalarRows(self.domains)


def _as_session(session: _FakeSession) -> AsyncSession:
    return cast(AsyncSession, cast(Any, session))


def _domain(domain: str, classification: DomainClass) -> ApprovedDomain:
    return ApprovedDomain(
        id=uuid4(),
        org_id=ORG_ID,
        domain=domain,
        direction="both",
        classification=classification,
        created_at=datetime.now(UTC),
    )


async def _classify(domains: list[ApprovedDomain], recipients: list[str]) -> list[str]:
    hits = await _classify_recipients(_as_session(_FakeSession(domains)), ORG_ID, recipients)
    return [hit.classification for hit in hits]


@pytest.mark.unit
@pytest.mark.parametrize("reversed_order", [False, True])
async def test_the_longest_matching_domain_wins_whatever_the_row_order(
    reversed_order: bool,
) -> None:
    domains = [_domain("hospital.in", "internal"), _domain("lab.hospital.in", "partner")]
    if reversed_order:
        domains.reverse()

    classified = await _classify(domains, ["tech@lab.hospital.in", "dr@hospital.in"])

    assert classified == ["approved_partner", "internal"]


@pytest.mark.unit
async def test_a_blocked_subdomain_beats_the_internal_parent() -> None:
    domains = [_domain("hospital.in", "internal"), _domain("leaks.hospital.in", "blocked")]

    classified = await _classify(domains, ["someone@leaks.hospital.in"])

    assert classified == ["blocked"]


@pytest.mark.unit
async def test_the_same_domain_configured_twice_settles_on_the_strictest() -> None:
    domains = [_domain("partnerlab.in", "partner"), _domain("partnerlab.in", "blocked")]

    assert await _classify(domains, ["a@partnerlab.in"]) == ["blocked"]

    domains.reverse()
    assert await _classify(domains, ["a@partnerlab.in"]) == ["blocked"]


@pytest.mark.unit
async def test_an_exact_address_is_more_specific_than_its_domain() -> None:
    domains = [_domain("hospital.in", "blocked"), _domain("director@hospital.in", "internal")]

    classified = await _classify(domains, ["director@hospital.in", "intern@hospital.in"])

    assert classified == ["internal", "blocked"]


@pytest.mark.unit
async def test_an_unconfigured_domain_keeps_its_default_class() -> None:
    domains = [_domain("hospital.in", "internal")]

    classified = await _classify(domains, ["patient@gmail.com", "someone@elsewhere.example"])

    assert classified == ["public_email", "external"]
