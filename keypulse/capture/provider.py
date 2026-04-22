from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class OCRRequest:
    app_name: str | None = None
    window_title: str | None = None
    process_name: str | None = None
    image_ref: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OCRResult:
    provider: str
    text: str | None
    ok: bool
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class OCRProvider(Protocol):
    name: str

    def is_available(self) -> bool:
        ...

    def recognize(self, request: OCRRequest) -> OCRResult:
        ...


def build_ocr_provider(name: str = "vision_native") -> OCRProvider:
    if name != "vision_native":
        raise ValueError(f"Unsupported OCR provider: {name}")

    from keypulse.capture.providers.vision_native import VisionNativeOCRProvider

    return VisionNativeOCRProvider()
