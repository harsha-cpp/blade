# pyright: reportUnknownMemberType=false

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from blade_backend.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "blade",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "blade_backend.tasks.scan_tasks",
        "blade_backend.tasks.maintenance_tasks",
    ],
)
REDELIVERY_MARGIN_SECONDS = 60
PURGE_HOUR_UTC = 3
PURGE_MINUTE_UTC = 17

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_soft_time_limit=settings.celery_task_soft_time_limit,
    task_time_limit=settings.celery_task_time_limit,
    worker_prefetch_multiplier=1,
    broker_transport_options={
        "visibility_timeout": settings.celery_task_time_limit + REDELIVERY_MARGIN_SECONDS
    },
    beat_schedule={
        "purge-expired-tokens": {
            "task": "blade.maintenance.purge_expired_tokens",
            "schedule": crontab(hour=PURGE_HOUR_UTC, minute=PURGE_MINUTE_UTC),
        }
    },
)
