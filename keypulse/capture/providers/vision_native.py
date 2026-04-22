from __future__ import annotations

from pathlib import Path
from typing import Any

from keypulse.capture.provider import OCRRequest, OCRResult


class VisionNativeOCRProvider:
    name = "vision_native"

    def __init__(self, vision_module: Any | None = None, quartz_module: Any | None = None):
        self._vision_module = vision_module
        self._quartz_module = quartz_module

    def _load_vision(self) -> Any | None:
        if self._vision_module is not None:
            return self._vision_module
        try:
            import Vision

            self._vision_module = Vision
        except Exception:
            self._vision_module = None
        return self._vision_module

    def _load_quartz(self) -> Any | None:
        if self._quartz_module is not None:
            return self._quartz_module
        try:
            import Quartz

            self._quartz_module = Quartz
        except Exception:
            self._quartz_module = None
        return self._quartz_module

    def is_available(self) -> bool:
        return self._load_vision() is not None and self._load_quartz() is not None

    def _load_cgimage_from_ref(self, image_ref: Any):
        quartz = self._load_quartz()
        if quartz is None or image_ref is None:
            return None, None

        if isinstance(image_ref, (str, Path)):
            path = str(image_ref)
            try:
                make_url = getattr(quartz, "NSURL_fileURLWithPath_", None)
                if callable(make_url):
                    url = make_url(path)
                else:
                    from Foundation import NSURL

                    url = NSURL.fileURLWithPath_(path)
                source = quartz.CGImageSourceCreateWithURL(url, None)
                if source is None:
                    return None, "invalid_image"
                image = quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
                return image, "path"
            except Exception:
                return None, "invalid_image"

        return image_ref, "direct"

    def recognize(self, request: OCRRequest) -> OCRResult:
        if not self.is_available():
            return OCRResult(
                provider=self.name,
                text=None,
                ok=False,
                reason="unavailable",
                metadata={"safe_fallback": True},
            )

        image, image_source = self._load_cgimage_from_ref(request.image_ref)
        if image is None:
            return OCRResult(
                provider=self.name,
                text=None,
                ok=False,
                reason=image_source or "missing_image",
                metadata={"safe_fallback": True},
            )

        try:
            vision = self._load_vision()
            recognize_request = vision.VNRecognizeTextRequest.alloc().init()
            set_recognition_level = getattr(recognize_request, "setRecognitionLevel_", None)
            accurate_level = getattr(vision, "VNRequestTextRecognitionLevelAccurate", None)
            if callable(set_recognition_level) and accurate_level is not None:
                set_recognition_level(accurate_level)
            set_language_correction = getattr(recognize_request, "setUsesLanguageCorrection_", None)
            if callable(set_language_correction):
                set_language_correction(True)

            handler = vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
            ok, error = handler.performRequests_error_([recognize_request], None)
            if not ok:
                return OCRResult(
                    provider=self.name,
                    text=None,
                    ok=False,
                    reason="vision_request_failed",
                    metadata={"error": str(error) if error else None, "image_source": image_source},
                )

            lines: list[str] = []
            for observation in recognize_request.results() or []:
                candidates = observation.topCandidates_(1) or []
                if not candidates:
                    continue
                candidate = candidates[0]
                candidate_text = candidate.string() if hasattr(candidate, "string") else str(candidate)
                candidate_text = candidate_text.strip()
                if candidate_text:
                    lines.append(candidate_text)

            text = "\n".join(lines).strip()
            if not text:
                return OCRResult(
                    provider=self.name,
                    text=None,
                    ok=False,
                    reason="no_text",
                    metadata={"image_source": image_source, "result_count": len(lines)},
                )

            return OCRResult(
                provider=self.name,
                text=text,
                ok=True,
                reason=None,
                metadata={"image_source": image_source, "result_count": len(lines)},
            )
        except Exception as exc:
            return OCRResult(
                provider=self.name,
                text=None,
                ok=False,
                reason="vision_exception",
                metadata={"error": str(exc), "image_source": image_source},
            )
