from __future__ import annotations

import plistlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from keypulse.sources.cleaning.path_filter import is_excluded_path
from keypulse.sources.types import ContentShape, DataSource, DataSourceInstance, SemanticEvent


_MAX_RESULTS = 200
_METADATA_KEYS = (
    "kMDItemDisplayName",
    "kMDItemLastUsedDate",
    "kMDItemFSContentChangeDate",
    "kMDItemContentModificationDate",
    "kMDItemFSCreationDate",
    "kMDItemAuthors",
    "kMDItemSubject",
    "kMDItemPath",
    "kMDItemContentType",
    "kMDItemContentTypeTree",
)
_TIME_FALLBACK_KEYS = (
    "kMDItemLastUsedDate",
    "kMDItemFSContentChangeDate",
    "kMDItemContentModificationDate",
    "kMDItemFSCreationDate",
)
_SPOTLIGHT_EXCLUDED_PATH_SEGMENTS = (
    "/library/containers/",
    "/library/caches/",
    "/library/application support/",
    "/library/cloudstorage/",
    "/.trash/",
    "__pycache__",
)
_SPOTLIGHT_EXCLUDED_CONTENT_TYPES = frozenset({
    "public.image",
    "public.video",
    "public.audio",
    "public.archive",
    "public.executable",
    "com.apple.application-bundle",
})


class SpotlightSource(DataSource):
    name = "spotlight"
    privacy_tier = "yellow"
    liveness = "always"
    description = "Spotlight 元数据源（mdfind + mdls，仅 metadata）"

    def discover(self) -> list[DataSourceInstance]:
        home = Path.home().expanduser().resolve(strict=False)
        candidates = [home / "Documents", home / "Downloads", home / "Desktop"]

        instances: list[DataSourceInstance] = []
        for root in candidates:
            if not root.exists() or not root.is_dir():
                continue
            instances.append(
                DataSourceInstance(
                    plugin=self.name,
                    locator=str(root.resolve(strict=False)),
                    label=root.name or "home",
                    metadata={"shape": ContentShape.DOCUMENT_FILE.value},
                )
            )
        return instances

    def read(
        self,
        instance: DataSourceInstance,
        since: datetime,
        until: datetime,
    ) -> Iterator[SemanticEvent]:
        root = Path(instance.locator).expanduser().resolve(strict=False)
        if not root.exists() or not root.is_dir():
            return iter(())

        since_utc = since.astimezone(timezone.utc)
        until_utc = until.astimezone(timezone.utc)

        def _iter_events() -> Iterator[SemanticEvent]:
            paths = _run_mdfind(root=root, since=since_utc)
            emitted = 0
            for path in paths:
                if emitted >= _MAX_RESULTS:
                    break
                if not path.exists() or not path.is_file():
                    continue
                if _is_excluded_spotlight_path(path):
                    continue
                excluded, _ = is_excluded_path(path)
                if excluded:
                    continue

                metadata = _run_mdls(path)
                if not metadata:
                    continue
                if _is_excluded_spotlight_content_type(metadata):
                    continue

                event_time = _event_time(metadata)
                if event_time is None or event_time < since_utc or event_time > until_utc:
                    continue

                display_name = str(metadata.get("kMDItemDisplayName") or "").strip()
                subject = str(metadata.get("kMDItemSubject") or "").strip()
                intent = display_name or subject or path.stem

                event = ContentShape.DOCUMENT_FILE.to_semantic_event(
                    {
                        "mtime": event_time,
                        "actor": "user",
                        "intent": intent,
                        "artifact": str(path),
                        "path": str(path),
                        "raw_ref": f"spotlight:{path}",
                        "metadata": _serialize_metadata_for_json({
                            **{key: metadata.get(key) for key in _METADATA_KEYS},
                            "shape": ContentShape.DOCUMENT_FILE.value,
                        }),
                    },
                    source=self.name,
                    privacy_tier=self.privacy_tier,
                )
                if event is None:
                    continue

                emitted += 1
                yield event

        return _iter_events()


def _run_mdfind(*, root: Path, since: datetime) -> list[Path]:
    since_utc = since.astimezone(timezone.utc)
    since_iso = since_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    time_expr = f"$time.iso({since_iso})"
    clauses = [f"{key} >= {time_expr}" for key in _TIME_FALLBACK_KEYS]
    query = f"({' || '.join(clauses)})"
    try:
        result = subprocess.run(
            ["mdfind", "-onlyin", str(root), query],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []

    paths: list[Path] = []
    seen: set[str] = set()
    for raw in (result.stdout or "").splitlines():
        text = raw.strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        paths.append(Path(text).expanduser().resolve(strict=False))
    return paths


def _run_mdls(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["mdls", "-plist", "-", str(path)],
            check=False,
            capture_output=True,
        )
    except Exception:
        return {}
    if result.returncode != 0 or not result.stdout:
        return {}

    try:
        payload = plistlib.loads(result.stdout)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _event_time(metadata: dict[str, Any]) -> datetime | None:
    for key in _TIME_FALLBACK_KEYS:
        parsed = _coerce_datetime(metadata.get(key))
        if parsed is not None:
            return parsed
    return None


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None

        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    parsed = None
            if parsed is None:
                return None

        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    return None


def _is_excluded_spotlight_path(path: Path) -> bool:
    normalized = str(path.expanduser().resolve(strict=False)).lower()
    return any(segment in normalized for segment in _SPOTLIGHT_EXCLUDED_PATH_SEGMENTS)


def _is_excluded_spotlight_content_type(metadata: dict[str, Any]) -> bool:
    tree = metadata.get("kMDItemContentTypeTree")
    if isinstance(tree, str):
        values = [tree]
    elif isinstance(tree, list):
        values = [item for item in tree if isinstance(item, str)]
    else:
        return False
    lowered = {value.strip().lower() for value in values if value.strip()}
    return any(item in lowered for item in _SPOTLIGHT_EXCLUDED_CONTENT_TYPES)


def _serialize_metadata_for_json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _serialize_metadata_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_metadata_for_json(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_serialize_metadata_for_json(item) for item in value)
    return value


__all__ = ["SpotlightSource"]
