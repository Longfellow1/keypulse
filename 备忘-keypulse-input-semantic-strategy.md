# KeyPulse 输入层语义内容采集策略优化

> 目标：在现有 Python daemon 架构、隐私红线不变的前提下，让 keyboard watcher 从"行为信号"升级为"行为 + 语义双信号"，解决中文 IME 造成的语义空洞问题。

---

## 一、现状诊断

### 当前采集链路

```
用户击键
  → CGEvent（Python + pyobjc / Quartz）
  → 拼音 / 英文字符流
  → 段落边界检测
  → emit keyboard_chunk (speaker: user)
  → SQLite raw_events
```

**问题所在**：CGEvent 捕获的是 IME 处理前的击键流。中文用户敲的是拼音，上屏的是汉字，但 KeyPulse 拿到的永远是拼音中间态。`zhegeshiying3wenti` 对下游 LLM 的语义密度约等于零。

### 已有的高质量语义通道

| 通道 | 现状 | 语义质量 |
|------|------|---------|
| keyboard watcher | 段落边界 + 拼音字符 | ❌ 中文场景语义丢失 |
| clipboard watcher | 已实现，有字段脱敏 | ✅ 用户主动保留的内容 |
| AX text (window/OCR) | speaker: system | ✅ 渲染后中文，但归属系统侧 |
| `keypulse save` 手动笔记 | 已实现 | ✅ 最高质量，原声 |

核心矛盾：**用户侧（speaker: user）的语义信号严重不足**，system 侧反而有中文，但在叙事 pipeline 里系统信号不能压过用户声音。

---

## 二、解法：三层叠加

不替换现有 keyboard watcher，在它之上叠加两个新信号源，组合成完整的用户语义层。

```
Layer 1  AX Value Diff Watcher   ← 新增，主力语义通道（上屏后中文）
Layer 2  Clipboard Watcher       ← 已有，补充权重标记
Layer 3  Keyboard Rhythm         ← 现有 keyboard watcher 降级为纯行为信号
```

三层信号都归属 `speaker: user`，在 pipeline 合并时按置信度加权。

---

## 三、Layer 1：AX Value Diff Watcher（核心新增）

### 原理

macOS AX（辅助功能）框架在 IME **提交之后**更新控件的 `AXValue`。对焦点文本控件挂 `AXValueChanged` observer，前后快照做 diff，得到的就是刚刚上屏的字符串——中文、英文都是渲染后的结果。

### Python 实现骨架

```python
# keypulse/watchers/ax_ime_watcher.py
import re
import difflib
import threading
from AppKit import NSWorkspace
from ApplicationServices import (
    AXObserverCreate, AXObserverAddNotification, AXObserverGetRunLoopSource,
    AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
    kAXValueChangedNotification, kAXSelectedTextChangedNotification,
    kAXFocusedUIElementChangedNotification,
    AXUIElementCopyAttributeNames,
)
import CoreFoundation as CF


POOR_AX_APPS = {
    # Electron 系支持差，走 Layer 3 快照兜底
    "com.microsoft.VSCode",
    "com.tinyspeck.slackmacgap",
    "notion.id",
}

# 候选词窗口特征：纯拼音 + 空格 + 数字，如 "nihao 1 2 3"
_CANDIDATE_PATTERN = re.compile(r'^[a-z\s]+[\d\s]*$')


class AXIMEWatcher:
    """
    监听 AXValueChanged，提取 IME 上屏后的真实字符。
    单实例，随 daemon 线程启动。
    """

    def __init__(self, emit_fn, privacy_filter):
        self._emit = emit_fn
        self._privacy = privacy_filter
        self._prev_value: str = ""
        self._prev_pid: int = -1
        self._observer = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def start(self):
        """注册工作区通知，监听焦点 App 变化。"""
        nc = NSWorkspace.sharedWorkspace().notificationCenter()
        nc.addObserver_selector_name_object_(
            self, "onAppActivated:", "NSWorkspaceDidActivateApplicationNotification", None
        )

    def onAppActivated_(self, notif):
        info = notif.userInfo()
        app = info.get("NSWorkspaceApplicationKey")
        if app:
            self._attach_to_pid(app.processIdentifier())

    # ------------------------------------------------------------------
    # 内部逻辑
    # ------------------------------------------------------------------

    def _attach_to_pid(self, pid: int):
        with self._lock:
            if pid == self._prev_pid:
                return
            self._flush_prev()
            self._prev_pid = pid
            self._prev_value = ""

            bundle = self._bundle_id_for_pid(pid)
            if bundle in POOR_AX_APPS:
                return  # 交给快照兜底

            try:
                app_elem = AXUIElementCreateApplication(pid)
                self._observer = self._create_observer(pid, app_elem)
            except Exception:
                pass  # 无障碍权限未授权等情况静默跳过

    def _create_observer(self, pid, app_elem):
        def callback(observer, element, notification, _refcon):
            self._on_ax_change(element)

        observer_ref, _ = AXObserverCreate(pid, callback)
        for notif in (kAXValueChangedNotification,
                      kAXSelectedTextChangedNotification,
                      kAXFocusedUIElementChangedNotification):
            AXObserverAddNotification(observer_ref, app_elem, notif, None)

        source = AXObserverGetRunLoopSource(observer_ref)
        CF.CFRunLoopAddSource(CF.CFRunLoopGetMain(), source, CF.kCFRunLoopDefaultMode)
        return observer_ref

    def _on_ax_change(self, element):
        curr = self._get_ax_value(element) or ""
        with self._lock:
            committed = self._extract_committed(self._prev_value, curr)
            if committed:
                clean = self._privacy.redact(committed)
                if clean:
                    self._emit({
                        "text": clean,
                        "speaker": "user",
                        "source": "ax_ime_commit",
                        "semantic_weight": 0.8,
                    })
            self._prev_value = curr

    def _extract_committed(self, prev: str, curr: str) -> str:
        """从 diff 中提取新增的上屏内容，过滤候选词状态。"""
        if _CANDIDATE_PATTERN.match(curr.strip()):
            return ""  # 还在候选词状态，未上屏
        if curr.startswith(prev):
            delta = curr[len(prev):]
        else:
            # 光标中间插入，用 difflib 取插入部分
            matcher = difflib.SequenceMatcher(None, prev, curr, autojunk=False)
            delta = "".join(
                curr[j1:j2]
                for tag, _i1, _i2, j1, j2 in matcher.get_opcodes()
                if tag in ("insert", "replace")
            )
        return delta.strip()

    def _flush_prev(self):
        """焦点离开前，对上一个控件做最后一次 diff，防止尾部丢失。"""
        # 实现略，逻辑同 _on_ax_change
        pass

    @staticmethod
    def _get_ax_value(element) -> str | None:
        err, val = AXUIElementCopyAttributeValue(element, "AXValue", None)
        return str(val) if err == 0 and isinstance(val, str) else None

    @staticmethod
    def _bundle_id_for_pid(pid: int) -> str:
        from AppKit import NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        return app.bundleIdentifier() or "" if app else ""
```

### App 支持情况

| 场景 | AX Value Diff 可用？ | 说明 |
|------|--------------------|----|
| Terminal / iTerm2 | ✅ | 英文命令完整 |
| Safari 地址栏、表单 | ✅ | 中文上屏后完整 |
| Xcode 编辑器 | ✅ | 代码 + 注释 |
| TextEdit / Pages | ✅ | |
| VS Code | ⚠️ 部分 | Electron，走快照兜底 |
| 微信 / 钉钉 | ❌ 黑名单 | 不采集 |

---

## 四、Layer 2：Clipboard Watcher 权重增强

现有 clipboard watcher 已经能拿到中文内容，只需在 emit 时加上语义权重标记，让 pipeline 知道这条信号质量更高。

```python
# keypulse/watchers/clipboard_watcher.py（现有文件，仅增加 metadata）

def _on_clipboard_change(self, content: str, source_bundle: str):
    if len(content) < 10:           # 过滤误触
        return
    if self._privacy.is_sensitive(content):
        return

    zh_ratio = _zh_char_ratio(content)

    self._emit({
        "text": self._privacy.redact(content),
        "speaker": "user",
        "source": "clipboard",
        "semantic_weight": 1.0 if zh_ratio > 0.4 else 0.7,
        "metadata": {
            "zh_ratio": round(zh_ratio, 2),
            "source_app": source_bundle,
        },
    })


def _zh_char_ratio(text: str) -> float:
    zh = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return zh / max(len(text), 1)
```

---

## 五、Layer 3：Keyboard Watcher 职责收窄

现有 keyboard watcher **不需要改字符捕获逻辑**，只需明确它的新职责：输出行为节奏，不再试图提供字符语义。

```python
# emit 结构调整：去掉 text 字段，改为节奏指标

@dataclass
class TypingRhythm:
    started_at: float
    duration_sec: float
    burst_count: int        # 连续击键段数（停顿 > 500ms 切断）
    total_keystrokes: int
    source: str = "keyboard_rhythm"
    speaker: str = "user"
    semantic_weight: float = 0.0   # 纯行为，不参与语义合并
    # 不携带任何字符内容
```

行为节奏在 pipeline 里的价值：即便 AX 信号空白（Electron 应用、黑名单应用），叙事生成至少知道"这段时间用户在持续输入"，不会把活跃工作时段渲染成空白。

---

## 六、快照兜底（Electron / 不支持 AX 的 App）

对 `POOR_AX_APPS` 中的应用，定时对焦点控件做全量快照，3 秒一次。

```python
# keypulse/watchers/ax_snapshot_watcher.py

class AXSnapshotWatcher:
    INTERVAL = 3.0

    def tick(self, focused_element, bundle_id: str):
        if bundle_id not in POOR_AX_APPS:
            return  # Layer 1 已覆盖
        curr = AXIMEWatcher._get_ax_value(focused_element) or ""
        delta = self._extract_new(self._prev, curr)
        if delta and not _CANDIDATE_PATTERN.match(delta.strip()):
            self._emit({
                "text": delta,
                "speaker": "user",
                "source": "ax_snapshot_fallback",
                "semantic_weight": 0.4,   # 低于主通道，延迟 3s 且精度低
            })
        self._prev = curr
```

---

## 七、Pipeline 融合：去重 + 加权

四条通道汇入 SQLite 后，pipeline 在构建工作块时需要去重，防止同一段文字被多个通道重复计入。

```python
# keypulse/pipeline/chunk_merger.py

WEIGHT_ORDER = ["clipboard", "ax_ime_commit", "ax_snapshot_fallback", "keyboard_rhythm"]

def merge_chunks(chunks: list[dict], window_sec: float = 5.0) -> list[dict]:
    """
    同一时间窗口内，文本相似度 > 0.85 的块只保留权重最高的。
    keyboard_rhythm 永远保留（行为信号，不参与语义去重）。
    """
    semantic = [c for c in chunks if c["source"] != "keyboard_rhythm"]
    rhythm   = [c for c in chunks if c["source"] == "keyboard_rhythm"]

    merged = _dedup_by_similarity(semantic, window_sec)
    return merged + rhythm  # rhythm 不去重，直接追加


def _dedup_by_similarity(chunks: list[dict], window_sec: float) -> list[dict]:
    from difflib import SequenceMatcher
    kept = []
    for c in sorted(chunks, key=lambda x: WEIGHT_ORDER.index(x["source"])):
        duplicate = any(
            abs(c["ts"] - k["ts"]) < window_sec
            and SequenceMatcher(None, c["text"], k["text"]).ratio() > 0.85
            for k in kept
        )
        if not duplicate:
            kept.append(c)
    return kept
```

---

## 八、升级后的语义覆盖率

| 场景 | 升级前 | 升级后 |
|------|--------|--------|
| Terminal 英文命令 | ✅ CGEvent 可读 | ✅ AX Diff（更干净） |
| Safari 中文搜索 / 表单 | ❌ 拼音 | ✅ AX Diff 上屏后 |
| Xcode 中文注释 | ❌ 拼音 | ✅ AX Diff |
| VS Code 编辑 | ❌ 拼音 | ⚠️ 快照兜底（3s 延迟） |
| 任意 App 复制内容 | ✅ 剪贴板（已有） | ✅ + 权重标记 |
| `keypulse save` 手动笔记 | ✅ 最高质量 | ✅ 不变 |
| 黑名单应用 | ❌ 不采集 | ❌ 不采集（红线不动） |

**剩余盲区**：在不支持 AX 的应用里，用户输入了大量中文但没有复制——这部分依然拿不到。HUD 引导用户"写完重要段落粘一下"是现阶段最低成本的补偿路径，无需代码。

---

## 九、与现有架构的接入点

```
daemon 主进程
  ├── keyboard_watcher        → 改为输出 TypingRhythm（不动字符逻辑）
  ├── ax_watcher              → 拆分为：
  │     ├── ax_ime_watcher    ← 新增，Layer 1
  │     └── ax_snapshot_watcher ← 新增，Electron 兜底
  ├── clipboard_watcher       → 加 semantic_weight 字段
  └── manual_note_watcher     → 不变
  
SQLite raw_events
  └── 新增字段：semantic_weight REAL, ime_source TEXT

pipeline / chunk_merger       ← 新增模块，合并去重
pipeline / narrative          → 优先取 semantic_weight 高的块做主干
```

---

## 十、实施优先级

1. **先做 clipboard 权重标记**：改动最小，立即提升 pipeline 对高质量信号的识别，两个小时内可以上。

2. **再做 AX IME Watcher**：这是主力改动，需要测试 AX 权限授权流程和各 app 兼容性，建议单独分支。

3. **最后做快照兜底**：只影响 Electron 系应用，优先级最低，等主通道稳定后补。

4. **keyboard_watcher 职责收窄**：可以和 AX IME Watcher 同步推进，但不是阻塞项——现在的字符片段不影响行为节奏的准确性。
