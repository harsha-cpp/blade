from __future__ import annotations

from dataclasses import dataclass

MAX_UNPACKED_BYTES = 64 * 1024 * 1024
MAX_EMBEDDED_MEMBERS = 50
MAX_EXTRACTION_CHARS = 1_000_000
MAX_OCR_IMAGES = 50


@dataclass(slots=True)
class ExtractionBudget:
    unpacked_bytes: int = MAX_UNPACKED_BYTES
    members: int = MAX_EMBEDDED_MEMBERS
    characters: int = MAX_EXTRACTION_CHARS
    ocr_images: int = MAX_OCR_IMAGES

    def take_member(self) -> bool:
        if self.members <= 0:
            return False
        self.members -= 1
        return True

    def take_bytes(self, count: int) -> bool:
        if count > self.unpacked_bytes:
            return False
        self.unpacked_bytes -= count
        return True

    def take_characters(self, text: str) -> str:
        if len(text) <= self.characters:
            self.characters -= len(text)
            return text
        allowed = text[: self.characters]
        self.characters = 0
        return allowed

    def take_ocr_images(self, count: int) -> int:
        allowed = min(count, self.ocr_images)
        self.ocr_images -= allowed
        return allowed
