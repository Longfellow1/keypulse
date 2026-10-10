# Daily Golden Schema — 双层产出规范

> 本文档定义 daily 层的"修好了"判据。M5 重构后 daily_summary.json 必须按此 schema 输出。

## 设计前提（已拍板）

- **Q1=C 双消费者**：daily 同时给"用户晨读"和"周报当原料"两个消费者
- **Q2=B 跨天主线**：主线属性不在单日，在周。周报先识别本周主线，daily 是它的"今日推进"子节点
- **Q3 真值**：5/6 = 用户认可黄金；5/9 = Opus 重写黄金（含问题诊断作为合理推进）

## 双层结构

```
daily_summary.json
├── topics              # 主线层 — 给周报吃，3-5 条
│   └── [
│         {
│           anchor: str,            # 链回本周主线表的 slug，如 "weekly-v3-rollout"
│           anchor_state: str,      # "continuing" | "started_today" | "completed_today"
│           narrative: str,         # 一段叙事，含事实+判断
│           decisions: [str],       # 当天关键决策
│           shipped: [str],         # 当天可见产出
│           events_ref: [str],      # 引用回 events 层 cluster_id 列表
│         }
│       ]
├── events              # 事件层 — 给晨读，保留全部 cluster
│   └── [
│         {
│           cluster_id: str,
│           display_name: str,
│           narrative_one_line: str,
│           event_count: int,
│           time_range: [str, str],
│           anchored_to: str | null,  # 归到哪条主线 / null = unanchored
│         }
│       ]
├── unanchored          # 噪音 / 穿插事件（events 中 anchored_to=null 的视图）
│   └── 渲染时单独成段，周报不消费
└── topic_status_snapshot   # （已有）跨天 topic 状态合并表
```

## 主线判定规则（mechanism, 不是 prompt）

一条 events 入 `topics` 还是 `unanchored`，由 `weekly_topic_anchor` 模块决定：

```
load(本周主线表 = ~/.keypulse/weekly-anchor.json)
↓
LLM 一次性看：今日 events + 本周主线表 → 输出归并方案
↓
归并方案 = {
  cluster_id: anchor_slug | "unanchored" | "new_anchor:<slug>"
}
↓
- 命中现有主线 → events.anchored_to = anchor_slug，进 topics
- new_anchor → 新主线候选（连续 ≥2 天才晋升正式主线，否则保留候选）
- unanchored → events.anchored_to = null，进 unanchored 视图
↓
回写本周主线表（last_active / daily_progress 追加）
```

## 渲染规则（daily.md）

- `## 今日要点`：基于 topics 层一句话+判断（不是模板拼接）
- `## 今天做的事`：每条 topic 一段，按 anchor 顺序，带"接 X 月 X 日 / 本周主线"上下文
- `## 没接住的球`：明确写空就空，不强填
- `## 一个观察`：跨天 / 跨主线视角的疑问句
- `## 跨周差异`：anchor_state 为 started_today / completed_today 的主线
- `## 明日的锚点`：用户填写槽位
- `## 今日 raw events (unanchored)`：unanchored 视图，给晨读
- `## 今日涉及的主题`：本日 anchor 链接

## 验收（daily_validator 5 类断言）

| 类 | 规则 | 黄金参考 |
|---|---|---|
| **结构** | topics 层 ≥ 1 条；events 层 ≥ 1 条；renderer 7 段都在 | 5/6 + 5/9 |
| **覆盖** | 每条 topic 必含：anchor、narrative ≥ 80 字、≥ 1 条 decision 或 shipped | 5/6 |
| **质感** | topics narrative 必含事实+判断组合；不许出现"XX 进入可继续迭代阶段"等 generic 复读句 | 5/6（无复读句） |
| **反模式** | "暂无可确认的"/"本周X有连续记录"/"修改X项目"主题名/单 event 升格成 topic | 5/6（不出现） |
| **客观性** | topics 中数字 / 工具名 / 日期可在 events 层溯源（5 级宽容度） | 5/6 |

## Anti-pattern（5/9 现状犯的错）

```
❌ 把 cluster 当主题段：10 cluster → 11 主题段（1 段 + 10 cluster）
❌ 单 event 升格成主题段："Chrome 收到新邮件提醒" 1 event 占一段
❌ 没跨天锚定：5/8 "和 Claude 讨论周报设计" 跟 5/9 "与 Claude 梳理项目状态" 是同一条主线，daily 不知道
❌ noise 不被识别：邮件提醒、登录页、chitchat 全升格成主题段
❌ 缺 unanchored 出口：所有 events 都被强行归到某主题段，没"事件归不到主线"的合法分类
```

## 测试样本

- `2026-05-06.md`（用户认可黄金）：5 个真主线段，无单 event 升格，narrative 含事实+判断
- `2026-05-09.md`（Opus 重写黄金）：1 主线段（v3 落地）+ unanchored 视图分开，noise 不污染 topics 层

## 修复路径（M5）

1. ✅ Step 1：5/6 + 5/9 落档黄金（本目录）
2. ⏳ Step 2：daily_validator.py + 跑 5/4-5/9 baseline 分数
3. ⏳ Step 3：weekly_topic_anchor.py 实现 + daily_summary schema 升级 + 删 importance_score 升格逻辑
4. ⏳ Step 4：5/4-5/9 全周回归 + 跑下次真链路验证
