# pyright: reportPrivateUsage=false

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.db.models import ApprovedDomain
from blade_backend.scan.api import _classify_sender

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


def _domain(domain: str, classification: str) -> ApprovedDomain:
    return ApprovedDomain(
        id=uuid4(),
        org_id=ORG_ID,
        domain=domain,
        direction="both",
        classification=classification,  # type: ignore[arg-type]
        created_at=datetime.now(UTC),
    )


async def _classify(domains: list[ApprovedDomain], sender: str) -> str:
    return await _classify_sender(_as_session(_FakeSession(domains)), ORG_ID, sender)


@pytest.mark.unit
@pytest.mark.parametrize("reversed_order", [False, True])
async def test_the_longest_matching_domain_wins_whatever_the_row_order(
    reversed_order: bool,
) -> None:
    domains = [_domain("hospital.in", "internal"), _domain("lab.hospital.in", "partner")]
    if reversed_order:
        domains.reverse()

    assert await _classify(domains, "tech@lab.hospital.in") == "approved_partner"
    assert await _classify(domains, "dr@hospital.in") == "internal"


@pytest.mark.unit
async def test_a_blocked_subdomain_beats_the_internal_parent() -> None:
    domains = [_domain("hospital.in", "internal"), _domain("leaks.hospital.in", "blocked")]

    assert await _classify(domains, "someone@leaks.hospital.in") == "external"


@pytest.mark.unit
async def test_the_same_domain_configured_twice_settles_on_the_strictest() -> None:
    domains = [_domain("partnerlab.in", "partner"), _domain("partnerlab.in", "blocked")]

    assert await _classify(domains, "a@partnerlab.in") == "external"

    domains.reverse()
    assert await _classify(domains, "a@partnerlab.in") == "external"


@pytest.mark.unit
async def test_an_exact_address_is_more_specific_than_its_domain() -> None:
    domains = [_domain("hospital.in", "blocked"), _domain("director@hospital.in", "internal")]

    assert await _classify(domains, "director@hospital.in") == "internal"
    assert await _classify(domains, "intern@hospital.in") == "external"


@pytest.mark.unit
async def test_an_unconfigured_domain_keeps_its_default_class() -> None:
    domains = [_domain("hospital.in", "internal")]

    assert await _classify(domains, "clinician@gmail.com") == "public_email"
    assert await _classify(domains, "someone@elsewhere.example") == "external"


@pytest.mark.unit
async def test_an_unparseable_sender_is_unknown() -> None:
    assert await _classify([_domain("hospital.in", "internal")], "  ") == "unknown"
    assert await _classify([_domain("hospital.in", "internal")], "no-at-sign") == "unknown"


@pytest.mark.unit
async def test_a_display_name_wrapped_address_is_still_matched() -> None:
    domains = [_domain("hospital.in", "internal")]

    assert await _classify(domains, "Dr Rao <dr@hospital.in>") == "internal"
