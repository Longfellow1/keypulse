"""M2-4 spike (#64): committed text of the focused text field via AXValue diffs.

Spike tool only. Not wired into the daemon, and it does not change the ax_text default.

This module is pure logic with no pyobjc import, so tests can run it with a fake adapter.
Every AX call goes through the AXSpikeAdapter protocol. The pyobjc implementation lives in
penpal/capture/ax_spike_adapter.py.

Pipeline per poll:
  focused element -> role filter (secure fields are never read) -> AXValue read
  -> remove IME marked text (AXMarkedTextRange) -> diff against the previous committed text
  -> keep only inserted text -> hygiene gate (D27) + desensitizer -> one JSON line.
"""
from __future__ import annotations

import json
import os
import queue
import re
import resource
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Callable, Protocol

from penpal.capture.hygiene import capture_gate, is_terminal_app
from penpal.privacy.desensitizer import desensitize, truncate

# Failure reasons (fixed strings, they end up in the summary line).
R_PERMISSION = "permission"
R_NO_FOCUS = "no_focused_element"
R_SECURE = "secure_text_field"
R_ROLE = "role_not_text_input"
R_VALUE = "value_unsupported"
R_READ = "read_error"
R_PAGE_URL_UNKNOWN = "page_url_unknown"

# Roles that can hold user-typed text. AXComboBox / AXSearchField are included because search
# boxes are common in the target apps. Anything else (AXWebArea, AXGroup, ...) is skipped.
TEXT_INPUT_ROLES = frozenset({"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"})
SECURE_ROLE = "AXSecureTextField"

PERMISSION_MISSING_MESSAGE = (
    "未授予辅助功能权限。请打开「系统设置 → 隐私与安全性 → 辅助功能」，"
    "把运行本命令的终端（例如「终端」或 iTerm）加入列表并打开开关，然后重新运行。"
)


def classify_element(role: str | None, subrole: str | None) -> str | None:
    """Return a failure reason if this element must not be read, otherwise None.

    Secure fields are checked first, on role AND subrole (Chrome reports password fields as
    AXTextField with subrole AXSecureTextField). The adapter calls this before any value read.
    """
    if role == SECURE_ROLE or subrole == SECURE_ROLE:
        return R_SECURE
    if role not in TEXT_INPUT_ROLES:
        return R_ROLE
    return None


# --- committed-text diff (pure) ---------------------------------------------------------------

def utf16_to_index(text: str, offset: int) -> int:
    """Map an AX range offset (UTF-16 code units) to a Python str index, clamped to the text.

    AX ranges count UTF-16 units, while Python counts code points. They only differ for
    characters outside the BMP (for example some emoji). CJK characters are one unit each.
    """
    units = 0
    for index, char in enumerate(text):
        if units >= offset:
            return index
        units += 2 if ord(char) > 0xFFFF else 1
    return len(text)


def committed_text(value: str, marked_range: tuple[int, int] | None) -> str:
    """Remove the IME marked (still-composing) span from AXValue.

    marked_range is (location, length) in UTF-16 units, as read from AXMarkedTextRange. When
    there is no composition, or the attribute is unsupported, the value is returned unchanged.
    Limitation: if the app keeps composing text out of AXValue, or does not expose
    AXMarkedTextRange, the pinyin is not removed. The spike reports that case as
    marked_range_unsupported_reads.
    """
    if not marked_range:
        return value
    location, length = marked_range
    if length <= 0:
        return value
    start = utf16_to_index(value, max(location, 0))
    end = utf16_to_index(value, max(location + length, 0))
    return value[:start] + value[end:]


def insertion_delta(previous: str, current: str) -> str:
    """Text inserted or appended between two snapshots. Deletions and replaced text give "" for the removed part.

    Algorithm: strip the common prefix and the common suffix, then return what is left in the
    current text. Only inserted text counts. A deletion leaves nothing in current, so it is
    ignored. A replacement counts only the new text.
    """
    if previous == current:
        return ""
    limit = min(len(previous), len(current))
    prefix = 0
    while prefix < limit and previous[prefix] == current[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and previous[-1 - suffix] == current[-1 - suffix]:
        suffix += 1
    return current[prefix:len(current) - suffix]


class CommittedTextTracker:
    """Keeps the last committed text per element. Switching element resets the baseline.

    The baseline reset matters: diffing a text field against a different field's text would
    emit the whole field as a "delta". An unknown element key (None) also resets, so the
    tracker never invents deltas. The cost is that those reads yield no output.
    """

    def __init__(self) -> None:
        self._key: Any = None
        self._text: str | None = None

    def observe(self, key: Any, committed: str) -> str:
        if key is None or self._text is None or key != self._key:
            self._key = key
            self._text = committed
            return ""
        delta = insertion_delta(self._text, committed)
        self._text = committed
        return delta


# --- adapter contract ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FocusRead:
    """Result of reading the focused element. Only role and subrole are read here, never the value."""

    ok: bool
    reason: str | None = None
    role: str | None = None
    subrole: str | None = None
    element_key: Any = None  # stable identity of the element, for the tracker
    handle: Any = None  # opaque adapter handle, never inspected by pure logic


@dataclass(frozen=True)
class TextRead:
    value: str | None  # raw AXValue, may contain IME marked text
    marked_range: tuple[int, int] | None  # (location, length) in UTF-16 units
    marked_supported: bool  # whether the element exposes AXMarkedTextRange at all
    window_title: str | None


class AXSpikeAdapter(Protocol):
    def is_trusted(self) -> bool: ...

    def find_app_pid(self, app_name: str) -> int | None: ...

    def app_name_for_pid(self, pid: int) -> str | None: ...

    def enable_accessibility(self, pid: int, app_name: str) -> dict[str, int]:
        """Set AXManualAccessibility (and AXEnhancedUserInterface for Chrome). Returns AXError codes, 0 = success."""
        ...

    def read_focused(self, pid: int) -> FocusRead: ...

    def read_text(self, read: FocusRead) -> TextRead: ...


# --- hygiene gate (D27) -----------------------------------------------------------------------

@dataclass(frozen=True)
class GateResult:
    text: str | None
    reason: str | None


GateFn = Callable[..., GateResult]


def make_hygiene_gate(
    *,
    redact_emails: bool,
    redact_phones: bool,
    redact_tokens: bool,
    drop_terminal_text: bool,
    max_text_length: int,
    title_deny_patterns,
    url_deny_hosts=(),
) -> GateFn:
    """Same order as CaptureManager: terminal rule, capture_gate (ax_text rules), then desensitize + truncate."""

    def gate(
        delta: str,
        app_name: str | None,
        window_title: str | None,
        page_url: str | None = None,
    ) -> GateResult:
        if drop_terminal_text and is_terminal_app(app_name):
            return GateResult(None, "terminal_app")
        verdict = capture_gate(
            source="ax_text",
            app_name=app_name,
            process_name=None,
            window_title=window_title,
            content_text=delta,
            title_deny_patterns=title_deny_patterns,
            page_url=page_url,
            url_deny_hosts=url_deny_hosts,
        )
        if verdict is not None:
            # Both DROP and METADATA_ONLY mean no content is written. The daemon would null the text too.
            return GateResult(None, verdict.reason)
        text = desensitize(
            delta,
            redact_emails=redact_emails,
            redact_phones=redact_phones,
            redact_tokens=redact_tokens,
        )
        text = truncate(text, max_text_length)
        if not text.strip():
            return GateResult(None, "empty_after_desensitize")
        return GateResult(text, None)

    return gate


# --- accounting -------------------------------------------------------------------------------

@dataclass
class SpikeStats:
    polls: int = 0
    reads_ok: int = 0
    reads_failed: Counter = field(default_factory=Counter)
    deltas: int = 0
    deltas_gated: Counter = field(default_factory=Counter)
    marked_range_unsupported_reads: int = 0
    marked_range_excluded_reads: int = 0

    def summary(
        self,
        *,
        app: str,
        cpu_seconds: float,
        manual_accessibility_set: bool,
        duration_sec: float,
    ) -> dict[str, Any]:
        return {
            "kind": "summary",
            "ts": _now_iso(),
            "app": app,
            "polls": self.polls,
            "reads_ok": self.reads_ok,
            "reads_failed": dict(sorted(self.reads_failed.items())),
            "deltas": self.deltas,
            "deltas_gated": dict(sorted(self.deltas_gated.items())),
            "marked_range_unsupported_reads": self.marked_range_unsupported_reads,
            "marked_range_excluded_reads": self.marked_range_excluded_reads,
            "cpu_seconds": round(cpu_seconds, 3),
            "manual_accessibility_set": bool(manual_accessibility_set),
            "duration_sec": round(duration_sec, 1),
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _handle_read(
    adapter: AXSpikeAdapter,
    read: FocusRead,
    *,
    app: str,
    tracker: CommittedTextTracker,
    gate: GateFn,
    stats: SpikeStats,
    sink: Callable[[dict[str, Any]], None],
    latency_ms: float,
    page_url: str | None,
) -> None:
    if not read.ok:
        stats.reads_failed[read.reason or R_READ] += 1
        return
    reason = classify_element(read.role, read.subrole)
    if reason is not None:
        # Secure fields and non-text roles stop here. read_text() is never called for them.
        stats.reads_failed[reason] += 1
        return

    text_read = adapter.read_text(read)
    if text_read.value is None:
        stats.reads_failed[R_VALUE] += 1
        return
    stats.reads_ok += 1
    if not text_read.marked_supported:
        stats.marked_range_unsupported_reads += 1
    if text_read.marked_range:
        stats.marked_range_excluded_reads += 1

    committed = committed_text(text_read.value, text_read.marked_range)
    delta = tracker.observe(read.element_key, committed)
    if not delta:
        return

    gated = gate(delta, app, text_read.window_title, page_url)
    if gated.text is None:
        stats.deltas_gated[gated.reason or "unknown"] += 1
        return
    stats.deltas += 1
    sink({
        "ts": _now_iso(),
        "kind": "delta",
        "app": app,
        "role": read.role,
        "delta": gated.text,
        "chars": len(gated.text),
        "latency_ms": round(latency_ms, 1),
    })


def run_spike(
    adapter: AXSpikeAdapter,
    *,
    pid: int,
    app: str,
    seconds: float,
    poll_interval_sec: float,
    gate: GateFn,
    sink: Callable[[dict[str, Any]], None],
    page_url_provider: Callable[[], str | None] | None = None,
    require_page_url: bool = False,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> SpikeStats:
    stats = SpikeStats()
    tracker = CommittedTextTracker()
    deadline = clock() + seconds
    try:
        while clock() < deadline:
            started = clock()
            stats.polls += 1
            page_url = page_url_provider() if page_url_provider is not None else None
            if require_page_url and not page_url:
                stats.reads_failed[R_PAGE_URL_UNKNOWN] += 1
            else:
                read = adapter.read_focused(pid)
                latency_ms = (clock() - started) * 1000.0
                _handle_read(
                    adapter, read, app=app, tracker=tracker, gate=gate,
                    stats=stats, sink=sink, latency_ms=latency_ms, page_url=page_url,
                )
            remaining = poll_interval_sec - (clock() - started)
            if remaining > 0:
                sleep(remaining)
    except KeyboardInterrupt:
        pass
    return stats


# --- output file --------------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")


def default_out_path(app: str, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    slug = _SLUG_RE.sub("-", app).strip("-") or "app"
    return Path("~/.penpal/spike").expanduser() / f"ax-{slug}-{stamp}.jsonl"


def git_worktree_root_for(path: Path) -> Path | None:
    """Return the work tree that contains path (nearest ancestor with a .git entry), or None.

    Works for paths that do not exist yet. A worktree's .git is a file, so exists() is used.
    """
    resolved = path.expanduser().resolve()
    for ancestor in resolved.parents:
        if (ancestor / ".git").exists():
            return ancestor
    return None


def open_spike_output(path: Path) -> IO[str]:
    """Create a new file with mode 0600. O_EXCL refuses to overwrite an existing file."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(fd, "w", encoding="utf-8")


def _cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


def _configured_browser_name(app_name: str, cfg: Any) -> str | None:
    candidates: list[str] = []
    for browser_config in (getattr(cfg, "browser", None), getattr(cfg, "browser_history", None)):
        values = getattr(browser_config, "supported_browsers", None) or getattr(browser_config, "browsers", None)
        if isinstance(values, (list, tuple)):
            candidates.extend(str(value) for value in values)

    url_browsers = getattr(getattr(cfg, "browser_url", None), "supported_browsers", None)
    if isinstance(url_browsers, (list, tuple)):
        candidates.extend(str(value) for value in url_browsers)

    target = app_name.strip().casefold()
    return next((name for name in candidates if name.strip().casefold() == target), None)


# --- CLI entry (called from penpal.cli) ---------------------------------------------------------

def run_ax_text_spike(
    *,
    app_name: str,
    seconds: float,
    out: str | None,
    adapter: AXSpikeAdapter,
    cfg_loader: Callable[[], Any],
    echo: Callable[[str], None],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Return a process exit code. 0 = ran, 1 = target app not found, 2 = refused before running."""
    # 1. Permission first, before anything else.
    if not adapter.is_trusted():
        echo(PERMISSION_MISSING_MESSAGE)
        return 2

    # 2. Output path: refuse anything inside a git work tree (the repo is public).
    out_path = Path(out).expanduser() if out else default_out_path(app_name)
    repo = git_worktree_root_for(out_path)
    if repo is not None:
        echo(f"拒绝写入 git 工作区内的路径（{repo}）。请使用仓库外的 --out，或省略 --out 使用默认路径。")
        return 2

    # 3. Config (after the permission check, so a missing permission never reads the config).
    cfg = cfg_loader()

    # 4. Target app.
    pid = adapter.find_app_pid(app_name)
    if pid is None:
        echo(f"未找到正在运行的应用：{app_name}。请先打开它，并把输入框聚焦。")
        return 1

    app_name_for_pid = getattr(adapter, "app_name_for_pid", None)
    resolved_app_name = app_name_for_pid(pid) if callable(app_name_for_pid) else None
    browser_name = _configured_browser_name(resolved_app_name or app_name, cfg)
    page_url_provider: Callable[[], str | None] | None = None
    if browser_name is not None:
        from penpal.capture.watchers.browser_url import BrowserUrlWatcher

        browser_url_watcher = BrowserUrlWatcher(queue.Queue(), supported_browsers=[browser_name])

        def page_url_provider() -> str | None:
            current = browser_url_watcher.read_browser_tab(browser_name)
            return current[0] if current is not None else None

        if not page_url_provider():
            echo("无法确认当前浏览器页面 URL，已拒绝采样。请确认当前标签页可读取。")
            return 2

    # 5. Electron / Chrome accessibility switches.
    codes = adapter.enable_accessibility(pid, app_name)
    manual_set = codes.get("AXManualAccessibility") == 0
    for attr, code in sorted(codes.items()):
        echo(f"{attr}: {'成功' if code == 0 else f'失败（AXError {code}）'}")

    try:
        handle = open_spike_output(out_path)
    except FileExistsError:
        echo(f"输出文件已存在，拒绝覆盖：{out_path}")
        return 2

    gate = make_hygiene_gate(
        redact_emails=cfg.privacy.redact_emails,
        redact_phones=cfg.privacy.redact_phones,
        redact_tokens=cfg.privacy.redact_tokens,
        drop_terminal_text=cfg.privacy.drop_terminal_text,
        max_text_length=cfg.clipboard.max_text_length,
        title_deny_patterns=cfg.privacy.title_deny_patterns,
        url_deny_hosts=cfg.privacy.url_deny_hosts,
    )

    def sink(record: dict[str, Any]) -> None:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()

    echo(f"开始采样 {app_name}，{seconds:g} 秒，输出 {out_path}")
    cpu_start = _cpu_seconds()
    started = clock()
    with handle:
        stats = run_spike(
            adapter,
            pid=pid,
            app=app_name,
            seconds=seconds,
            poll_interval_sec=cfg.ax_text.poll_interval_sec,
            gate=gate,
            sink=sink,
            page_url_provider=page_url_provider,
            require_page_url=browser_name is not None,
            clock=clock,
            sleep=sleep,
        )
        summary = stats.summary(
            app=app_name,
            cpu_seconds=_cpu_seconds() - cpu_start,
            manual_accessibility_set=manual_set,
            duration_sec=clock() - started,
        )
        sink(summary)
    echo(json.dumps(summary, ensure_ascii=False))
    return 0
