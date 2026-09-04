from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status

INVALID_CURSOR_DETAIL = "invalid cursor"
MAX_PAGE_SIZE = 200


def empty_page_items[ItemT](item_type: type[ItemT]) -> Callable[[], list[ItemT]]:
    del item_type

    def factory() -> list[ItemT]:
        return []

    return factory


def encode_cursor(created_at: datetime, identifier: UUID) -> str:
    raw = f"{created_at.isoformat()}|{identifier}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        timestamp, _, identifier = raw.partition("|")
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed, UUID(identifier)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=INVALID_CURSOR_DETAIL) from exc
