from __future__ import annotations

import asyncio
import os
import signal
import tempfile
import threading
from collections import deque
from collections.abc import Generator
from concurrent.futures import BrokenExecutor, Future, ProcessPoolExecutor
from contextlib import contextmanager, suppress
from ctypes import c_long
from multiprocessing import get_context
from multiprocessing.context import SpawnContext
from multiprocessing.sharedctypes import Synchronized
from pathlib import Path

import structlog
from blade_detection.api import detect_email
from blade_detection.models import Attachment, EmailPayload, ScanResult

from blade_backend.settings import get_settings

logger = structlog.get_logger(__name__)

_WARM_UP_PAYLOAD = EmailPayload(
    subject="Warm up",
    body="Ward rota for Dr Mehta, nothing sensitive in here.",
)


class DetectionTimeoutError(Exception):
    pass


class DetectionUnavailableError(Exception):
    pass


def _start_detection_worker(pid_slot: Synchronized[int]) -> None:
    pid_slot.value = os.getpid()
    detect_email(_WARM_UP_PAYLOAD)


def _run_detection_job(payload_json: str) -> str:
    return detect_email(EmailPayload.model_validate_json(payload_json)).model_dump_json()


class _WorkerSlot:
    def __init__(self, context: SpawnContext, max_tasks_per_worker: int) -> None:
        self._context = context
        self._max_tasks_per_worker = max_tasks_per_worker
        self._pid: Synchronized[int] = context.Value(c_long, 0)
        self._executor: ProcessPoolExecutor | None = None

    def submit(self, payload_json: str) -> Future[str]:
        if self._executor is None:
            self._executor = ProcessPoolExecutor(
                max_workers=1,
                mp_context=self._context,
                initializer=_start_detection_worker,
                initargs=(self._pid,),
                max_tasks_per_child=self._max_tasks_per_worker,
            )
        return self._executor.submit(_run_detection_job, payload_json)

    def kill_worker(self) -> int:
        pid = self._pid.value
        if pid:
            with suppress(OSError):
                os.kill(pid, signal.SIGKILL)
        self.discard()
        return pid

    def discard(self) -> None:
        executor = self._executor
        self._executor = None
        self._pid.value = 0
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


class DetectionPool:
    def __init__(self, size: int, max_tasks_per_worker: int) -> None:
        self.size = size
        self.max_tasks_per_worker = max_tasks_per_worker
        self._context = get_context("spawn")
        self._lock = threading.Lock()
        self._free: deque[_WorkerSlot] = deque(
            _WorkerSlot(self._context, max_tasks_per_worker) for _ in range(size)
        )

    def acquire(self) -> _WorkerSlot:
        with self._lock:
            if self._free:
                return self._free.pop()
        return _WorkerSlot(self._context, self.max_tasks_per_worker)

    def release(self, slot: _WorkerSlot) -> None:
        with self._lock:
            if len(self._free) < self.size:
                self._free.append(slot)
                return
        slot.discard()

    def shutdown(self) -> None:
        with self._lock:
            slots = list(self._free)
            self._free.clear()
        for slot in slots:
            slot.discard()


_semaphore: asyncio.Semaphore | None = None
_semaphore_limit: int | None = None
_pool: DetectionPool | None = None
_pool_lock = threading.Lock()


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore, _semaphore_limit  # noqa: PLW0603 - process-wide by design
    limit = get_settings().scan_max_concurrency
    if _semaphore is None or _semaphore_limit != limit:
        _semaphore = asyncio.Semaphore(limit)
        _semaphore_limit = limit
    return _semaphore


def get_detection_pool() -> DetectionPool:
    global _pool  # noqa: PLW0603 - process-wide by design
    settings = get_settings()
    with _pool_lock:
        stale = _pool is not None and (
            _pool.size != settings.scan_max_concurrency
            or _pool.max_tasks_per_worker != settings.scan_worker_max_tasks
        )
        if stale and _pool is not None:
            _pool.shutdown()
            _pool = None
        if _pool is None:
            _pool = DetectionPool(
                settings.scan_max_concurrency,
                settings.scan_worker_max_tasks,
            )
        return _pool


def shutdown_detection_pool() -> None:
    global _pool  # noqa: PLW0603 - process-wide by design
    with _pool_lock:
        pool = _pool
        _pool = None
    if pool is not None:
        pool.shutdown()


async def run_detection(payload: EmailPayload) -> ScanResult:
    settings = get_settings()
    async with _get_semaphore():
        if not settings.scan_process_pool_enabled:
            return await asyncio.to_thread(detect_email, payload)
        return await run_in_detection_pool(
            payload.model_dump_json(),
            deadline_seconds=settings.scan_deadline_seconds,
        )


async def run_in_detection_pool(payload_json: str, *, deadline_seconds: float) -> ScanResult:
    pool = get_detection_pool()
    slot = pool.acquire()
    try:
        future = await asyncio.to_thread(slot.submit, payload_json)
        result_json = await asyncio.wait_for(asyncio.wrap_future(future), deadline_seconds)
    except TimeoutError as exc:
        killed_pid = slot.kill_worker()
        logger.warning(
            "detection exceeded its deadline",
            deadline_seconds=deadline_seconds,
            killed_pid=killed_pid,
        )
        raise DetectionTimeoutError("detection exceeded its deadline") from exc
    except BrokenExecutor as exc:
        slot.discard()
        logger.warning("detection worker died", reason=type(exc).__name__)
        raise DetectionUnavailableError("detection worker died") from exc
    finally:
        pool.release(slot)
    return ScanResult.model_validate_json(result_json)


async def scan_text(subject: str, body: str, recipients: list[str]) -> ScanResult:
    return await run_detection(
        EmailPayload(subject=subject, body=body, recipients=recipients, attachments=[])
    )


async def scan_attachment_bytes(
    data: bytes,
    *,
    attachment_id: str,
    filename: str,
    mime_type: str,
    sha256: str,
) -> ScanResult:
    with _temporary_attachment(data, filename) as path:
        return await run_detection(
            EmailPayload(
                attachments=[
                    Attachment(
                        id=attachment_id,
                        filename=filename,
                        mime_type=mime_type,
                        size_bytes=len(data),
                        sha256=sha256,
                        local_path=str(path),
                    )
                ]
            )
        )


@contextmanager
def _temporary_attachment(data: bytes, filename: str) -> Generator[Path]:
    settings = get_settings()
    settings.attachment_temp_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
        mode="wb",
        suffix=Path(filename).suffix[:20],
        prefix="scan-",
        dir=settings.attachment_temp_dir,
        delete=False,
    )
    path = Path(handle.name)
    try:
        with handle:
            handle.write(data)
        yield path
    finally:
        path.unlink(missing_ok=True)
