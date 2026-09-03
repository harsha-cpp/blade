from __future__ import annotations

from collections.abc import Iterator

import pytest
from blade_detection.models import EmailPayload

from blade_backend.scan.runner import (
    DetectionTimeoutError,
    run_detection,
    run_in_detection_pool,
    shutdown_detection_pool,
)
from blade_backend.settings import get_settings

BODY = "Patient Lakshmi Devi, UHID 0024518, diagnosis E11.9. Aadhaar 7534 7930 7460."


@pytest.fixture(name="_single_worker_pool")
def single_worker_pool(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SCAN_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("SCAN_PROCESS_POOL_ENABLED", "true")
    get_settings.cache_clear()
    shutdown_detection_pool()
    yield
    shutdown_detection_pool()
    monkeypatch.undo()
    get_settings.cache_clear()


@pytest.mark.unit
@pytest.mark.usefixtures("_single_worker_pool")
async def test_a_job_past_the_deadline_is_killed_and_the_slot_stays_usable() -> None:
    payload = EmailPayload(subject="Discharge summary", body=BODY).model_dump_json()

    with pytest.raises(DetectionTimeoutError):
        await run_in_detection_pool(payload, deadline_seconds=0.2)

    recovered = await run_in_detection_pool(payload, deadline_seconds=120)

    assert "IN_AADHAAR" in {entity.type for entity in recovered.entities}


@pytest.mark.unit
async def test_detection_runs_inline_when_the_process_pool_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCAN_PROCESS_POOL_ENABLED", "false")
    get_settings.cache_clear()
    try:
        result = await run_detection(EmailPayload(subject="Rota", body=BODY))
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()

    assert "IN_AADHAAR" in {entity.type for entity in result.entities}
