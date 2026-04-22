# KeyPulse 轻 OCR 与多源正文采集设计

## 目标

在不引入 VLM、不显著增加常驻开销的前提下，让 KeyPulse 能够捕获前台窗口里的正文变化，并与键入痕迹做去重融合。

主链路固定为：

`AX 文本 -> Vision OCR -> keyboard chunk -> canonical merge`

其中：

- `AX 文本` 是首选正文来源
- `Vision OCR` 是 macOS 本地默认兜底
- `keyboard chunk` 只做编辑痕迹和增量对照，不做原始 keylogger

## 第一阶段范围

这一阶段只做必要骨架，不追求一次到位：

1. 增加 OCR / keyboard 的配置结构
2. 增加 OCR provider 抽象和 `vision_native` 默认实现入口
3. 增加前台窗口文本采集 watcher 骨架，优先尝试 AX
4. 增加 keyboard chunk watcher 骨架
5. 增加轻量去重融合器，把多源内容归并为 `canonical_event`

暂不做：

- 全屏 OCR
- 高频 OCR
- VLM 推理
- RapidOCR / Paddle 作为默认依赖
- 复杂 UI 交互或可视化配置面板

## 采集源优先级

优先级从高到低：

1. `manual`
2. `clipboard`
3. `ax_text`
4. `ocr_text`
5. `keyboard_chunk`

规则：

- 高优先源和低优先源高相似时，只保留高优先源为主记录
- 低优先源转为辅助证据，写入 `metadata_json`
- 如果低优先源包含明确增量，只保留增量片段

## OCR 选型

默认 OCR provider 为 `vision_native`。

原因：

- macOS 自带
- 本地离线
- 不需要额外模型分发
- 常驻服务开销最小

后续预留 `rapidocr_onnx` provider 插槽，但不在第一阶段启用。

## 触发策略

OCR 不做固定高频轮询，只做事件驱动：

1. `窗口切换` 后延迟 `0.8s` 尝试一次
2. `窗口稳定停留` 超过 `10s` 且内容签名变化时尝试一次
3. `键入静默` 超过 `2s` 且 AX 无法取到正文时尝试一次

明确禁止：

- 连续输入过程中频繁 OCR
- 前台窗口未变化时重复 OCR
- deny 应用或安全输入场景触发 OCR

## AX 与键入策略

AX watcher 目标：

- 只看前台应用
- 尝试读取 `AXFocusedUIElement`、`AXValue`、`AXSelectedText`、静态文本
- 拿不到时返回空，不阻塞链路

keyboard watcher 目标：

- 只生成 chunk，不记录逐键日志
- `2s` 静默切块
- `10s` 强制切块
- 默认仅用于去重和增量判断

## Canonical Merge

增加一层轻量融合器，对候选文本做归并。

比较维度：

- `source_priority`
- `time_overlap`
- `same_app`
- `same_window`
- `normalized_hash`
- `similarity_ratio`

输出原则：

- 主记录写入 `content_text`
- 辅助来源写入 `metadata_json.sources`
- 去重原因写入 `metadata_json.merge_reason`

## 隐私边界

必须继承现有 policy：

- `deny` 直接跳过
- `metadata-only` 不保留正文
- 安全输入框不采
- 敏感应用不跑 OCR

keyboard chunk 默认允许未来降级为只存 hash 或摘要，不强制长期保存原文。

## 第一阶段验收标准

1. 不引入新的重型依赖
2. 默认配置下服务可以正常启动
3. deny / metadata-only 场景不泄漏正文
4. 多源事件能归并，不产生明显重复爆炸
5. 端到端能产出至少一条来自 AX 或 OCR 的正文事件

## 后续阶段

第二阶段再考虑：

- RapidOCR / PP-OCRv5-mobile 可选 fallback
- OCR 精度回退策略
- 更细的窗口白名单 / 黑名单
- Dashboard 中展示“正文来源”和“去重依据”
