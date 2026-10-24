"""First-use source choice for `penpal install onboard` (issue #32). Helpers only; the CLI prints and prompts.

Rules this module enforces:
- A source is read only if the user chose it. Choosing writes the existing per-source switch
  (`penpal source enable/disable`); every registered source that was not chosen is switched off.
- WeChat is not offered here: it is red-zone data with its own authorization (`penpal source wechat authorize`).
- A source that is not available is a status with a fix path, never an exception.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from penpal.capture.capture_control import set_source_enabled
from penpal.sources.availability import NOT_FOUND, PERMISSION_DENIED, PROBE_ERROR, Availability, unavailable
from penpal.sources.history_gate import mark_onboarded
from penpal.sources.importer import ImportResult
from penpal.sources.registry import discover_all, list_sources
from penpal.sources.types import DataSourceInstance
from penpal.utils.logging import get_logger

LOGGER = get_logger("sources.onboarding")

# Not part of the source choice: has its own authorization step.
EXCLUDED_FROM_CHOICE = frozenset({"wechat"})
# How far back the first import reads for sources that are not cursor-based. None = no lower bound: the first
# import takes everything the source still holds, because keeping records past the source's own retention is the
# point. Batching and resume make a large first import safe. Set a number of days to cap it.
FIRST_IMPORT_LOOKBACK_DAYS: int | None = None
# Lower bound used when there is no cap (the earliest timestamp any plugin reads).
IMPORT_SINCE_FLOOR = datetime(1970, 1, 1, tzinfo=timezone.utc)


def first_import_since(now: datetime, days: int | None = FIRST_IMPORT_LOOKBACK_DAYS) -> datetime:
    if days is None:
        return IMPORT_SINCE_FLOOR
    return now - timedelta(days=days)

PANE_FULL_DISK_ACCESS = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
PANE_FILES_AND_FOLDERS = "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders"

# Source -> the macOS permission it needs. Only sources whose originals sit in TCC-protected places are listed.
# chrome_history is not verified on a real Mac yet: it may need only the Files and Folders grant instead.
SOURCE_PERMISSION_NEEDS: dict[str, tuple[str, ...]] = {
    "safari_history": ("full_disk_access",),
    "knowledgec": ("full_disk_access",),
    "chrome_history": ("full_disk_access",),
}
PERMISSION_LABELS: dict[str, str] = {
    "full_disk_access": "完全磁盘访问（Full Disk Access）",
}
PERMISSION_PANES: dict[str, str] = {
    "full_disk_access": PANE_FULL_DISK_ACCESS,
}

BOUNDARY_LINES: tuple[str, ...] = (
    "Penpal 只在本机读取你选择的来源，数据库保存在本机 ~/.penpal。",
    "没有选中的来源不会被读取；之后可用 `penpal source enable/disable NAME` 修改。",
    "核心不调用大模型，原始内容不会发往云端。AI 客户端只能读取你授权的分桶。",
    "微信属于红区数据，不在这里授权，需单独执行 `penpal source wechat authorize`。",
)


@dataclass(frozen=True)
class SourceOffer:
    name: str
    description: str
    instances: tuple[DataSourceInstance, ...]


def offered_sources() -> list[SourceOffer]:
    """Registered sources that found data on this Mac, in name order. Sources with nothing found are left out
    (they are switched off by apply_choice)."""
    found = discover_all()
    offers: list[SourceOffer] = []
    for plugin in list_sources():
        if plugin.name in EXCLUDED_FROM_CHOICE:
            continue
        instances = tuple(found.get(plugin.name) or ())
        if instances:
            offers.append(SourceOffer(name=plugin.name, description=plugin.description or "", instances=instances))
    return offers


def choosable_names() -> set[str]:
    return {plugin.name for plugin in list_sources() if plugin.name not in EXCLUDED_FROM_CHOICE}


def apply_choice(chosen: Iterable[str]) -> dict[str, bool]:
    """Write the switches: chosen sources on, every other registered source off. Returns name -> enabled."""
    chosen_set = set(chosen)
    applied: dict[str, bool] = {}
    mark_onboarded("onboard")  # any explicit choice lifts the historical-import gate (#32)
    for plugin in list_sources():
        if plugin.name in EXCLUDED_FROM_CHOICE:
            continue
        enabled = plugin.name in chosen_set
        set_source_enabled(plugin.name, enabled)
        applied[plugin.name] = enabled
    return applied


def permissions_for(names: Iterable[str]) -> list[tuple[str, str]]:
    """(source, permission key) pairs the chosen sources need, in the order given."""
    pairs: list[tuple[str, str]] = []
    for name in names:
        for key in SOURCE_PERMISSION_NEEDS.get(name, ()):
            pairs.append((name, key))
    return pairs


def check_source(name: str, plugin_by_name: dict[str, object]) -> Availability:
    """Re-check one source's original with its availability probe (the same probe doctor uses)."""
    plugin = plugin_by_name.get(name)
    if plugin is None:
        return unavailable(NOT_FOUND)
    try:
        return plugin.availability()  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.warning("availability failed for %s: %s", name, exc)
        return unavailable(PROBE_ERROR)


def source_problems(enabled_names: Iterable[str]) -> list[dict[str, str]]:
    """Enabled sources that cannot be read now, with the fix path. `not_found` (no data here) is not a fault."""
    by_name = {plugin.name: plugin for plugin in list_sources()}
    rows: list[dict[str, str]] = []
    for name in sorted(set(enabled_names)):
        if name not in by_name:
            continue
        state = check_source(name, by_name)
        if state.available or state.reason == NOT_FOUND:
            continue
        rows.append({
            "source": name,
            "reason": state.reason or PROBE_ERROR,
            "fix": fix_hint(name, state.reason or PROBE_ERROR),
            "pane": pane_for(name, state.reason or PROBE_ERROR),
        })
    return rows


def pane_for(name: str, reason: str) -> str:
    if reason != PERMISSION_DENIED:
        return ""
    if any(key == "full_disk_access" for key in SOURCE_PERMISSION_NEEDS.get(name, ())):
        return PANE_FULL_DISK_ACCESS
    return PANE_FILES_AND_FOLDERS


def fix_hint(name: str, reason: str) -> str:
    if reason == PERMISSION_DENIED:
        if pane_for(name, reason) == PANE_FULL_DISK_ACCESS:
            return "系统设置 → 隐私与安全性 → 完全磁盘访问，勾选运行 Penpal 的程序；然后运行 penpal doctor 复查。"
        return "系统设置 → 隐私与安全性 → 文件和文件夹，允许运行 Penpal 的程序；然后运行 penpal doctor 复查。"
    if reason == "file_missing":
        return "来源文件不存在：先在对应 App 里产生一次记录，或用 penpal source discover 确认路径。"
    if reason == "db_locked":
        return "来源数据库被占用：稍后重试 penpal source import。"
    if reason == "unsupported_version":
        return "来源格式与当前读取器不兼容：本版本不读取该文件。"
    if reason == PROBE_ERROR:
        return "探测失败，原因已写入日志；检查日志后重试。"
    return "运行 penpal source discover 查看来源状态。"


def describe_instances(offer: SourceOffer) -> str:
    if len(offer.instances) == 1:
        return offer.instances[0].label or offer.instances[0].locator
    return f"{len(offer.instances)} 项"


def format_import_result(result: ImportResult) -> str:
    if result.status == "skipped":
        if result.reason == "switched_off":
            return "已关闭，未导入。"
        return f"未导入：{result.reason}（{fix_hint(result.source, result.reason or '')}）"
    resumed = "；从上次游标续传" if result.resumed else ""
    if result.status == "done":
        return f"完成：{result.batches} 批，本次 {result.events} 条{resumed}。"
    if result.status == "failed":
        return f"写入失败：已保留上一批游标，重新运行即可续传{resumed}。"
    return f"中断：{result.batches} 批已入库{resumed}。"
