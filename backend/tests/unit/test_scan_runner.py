from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator

import pytest
from blade_detection.models import EmailPayload
from structlog.testing import EventDict, capture_logs

from blade_backend.scan.runner import (
    DetectionTimeoutError,
    run_detection,
    run_in_detection_pool,
    shutdown_detection_pool,
)
from blade_backend.settings import get_settings

BODY = "Patient Lakshmi Devi, UHID 0024518, diagnosis E11.9. Aadhaar 7534 7930 7460."
SLOW_BODY = BODY * 4_000
DEADLINE_REPORTED = "detection exceeded its deadline"
EXIT_WAIT_SECONDS = 10.0


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


def _killed_pid(events: list[EventDict]) -> int:
    for event in events:
        if event.get("event") == DEADLINE_REPORTED:
            return int(event["killed_pid"])
    raise AssertionError(f"no {DEADLINE_REPORTED!r} was logged")


def _has_stopped_running(pid: int) -> bool:
    deadline = time.monotonic() + EXIT_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if not state or state.startswith("Z"):
            return True
        time.sleep(0.05)
    return False


@pytest.mark.unit
@pytest.mark.usefixtures("_single_worker_pool")
async def test_a_job_past_the_deadline_is_killed_and_the_slot_stays_usable() -> None:
    payload = EmailPayload(subject="Discharge summary", body=BODY).model_dump_json()
    slow_payload = EmailPayload(subject="Discharge summary", body=SLOW_BODY).model_dump_json()

    warmed = await run_in_detection_pool(payload, deadline_seconds=120)
    assert "IN_AADHAAR" in {entity.type for entity in warmed.entities}

    with capture_logs() as events, pytest.raises(DetectionTimeoutError):
        await run_in_detection_pool(slow_payload, deadline_seconds=0.5)

    killed_pid = _killed_pid(events)
    assert killed_pid > 0
    assert _has_stopped_running(killed_pid)

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
