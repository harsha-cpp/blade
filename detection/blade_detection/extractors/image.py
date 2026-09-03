from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO

import structlog
from PIL import Image, UnidentifiedImageError

from blade_detection.extractors.issues import ExtractionIssue, no_issues

logger = structlog.get_logger(__name__)
MAX_IMAGE_PIXELS = 10000 * 10000


@dataclass(frozen=True, slots=True)
class ImageContent:
    image: Image.Image | None = None
    issues: list[str] = field(default_factory=no_issues)


def open_image(data: bytes) -> ImageContent:
    try:
        image = Image.open(BytesIO(data))
        width, height = image.size
        if width * height > MAX_IMAGE_PIXELS:
            logger.warning("image attachment exceeds dimension limit")
            return ImageContent(issues=[ExtractionIssue.IMAGE_TOO_LARGE])
        image.load()
        return ImageContent(image.convert("RGB"))
    except (OSError, UnidentifiedImageError):
        logger.warning("image attachment extraction failed")
        return ImageContent(issues=[ExtractionIssue.IMAGE_UNREADABLE])
