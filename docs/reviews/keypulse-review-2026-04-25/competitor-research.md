# KeyPulse 竞品调研与产品方向

日期：2026-04-25

范围：个人知识库、AI second brain、本地优先记忆、lifelog、Obsidian/Markdown 工作流相关的开源或 open-core 项目。

## 结论摘要

KeyPulse 不应该正面竞争完整 PKM 编辑器，也不应该把自己做成泛化的“文档聊天 / RAG 应用”。更强的切入点是：本地优先的工作记忆编译器，把日常工作活动变成可审计、可编辑、可追溯来源的 Markdown 知识。

当前最大的产品风险不是功能少，而是隐私承诺和实现之间有缺口。评审里发现的 keyboard chunk 原文存储、metadata 未脱敏、隐私窗口排除未实现、`local-first` 可 fallback 到云端等问题，应该先修，再扩展采集范围或 AI 检索能力。

## Karpathy 方向的启发

Karpathy 公开的 “LLM Wiki” gist 值得参考，因为它强调的不是简单向量检索，而是从原始资料逐步编译出可读、可审计、可维护的 Markdown/wiki 语料。

核心思路可以概括为：

- 收集原始资料。
- 把资料蒸馏成结构化、人类可读的 Markdown。
- 保留到原始来源的链接。
- 随时间编译出更高层的 wiki 页面。
- 让这套语料同时服务人和模型上下文。

来源：[karpathy/LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

映射到 KeyPulse，类似管线应该是：

- 原始活动事件：app、browser、clipboard、accessibility text、window context、typed fragments。
- 标准化证据：脱敏、去重、带时间戳、带 source link 的事实。
- 每日笔记：人可 review 的摘要、决策和问题。
- 长期知识：项目页、人物页、主题页、重复工作模式、未解决问题。
- AI 层：先基于已整理的 Markdown 知识问答，再按需回查 raw events。

## 竞品格局

| 类别 | 代表项目 | 它们在做什么 | 价值怎么提供 | 对 KeyPulse 的启发 |
| --- | --- | --- | --- | --- |
| 本地优先 PKM / 知识编辑器 | Logseq、SiYuan、AFFiNE、TriliumNext、Dendron | 笔记、块结构、图谱、双链、本地或自托管存储 | 通过结构化写作和关系组织提升长期检索与思考 | 不要做另一个编辑器。重点输出到这些工具，尤其是 Markdown/Obsidian 工作流。 |
| AI second brain / 文档问答 | Khoj、AnythingLLM、PrivateGPT、Open WebUI、Reor、RAGFlow | 索引文件、网页、文档和聊天记录，用 LLM/RAG 回答问题 | 更快召回和综合已有资料 | KeyPulse 不能只做 RAG，必须提供更强的 provenance 和隐私边界。 |
| lifelog / 本地记忆 | OpenRecall、screenpipe、Rewind 类产品 | 持续截图、OCR、音频或活动捕获 | 不依赖手动记录，也能回忆发生过什么 | KeyPulse 应该比 always-on 截屏更克制：选择性采集、强脱敏、用户确认后再沉淀为知识。 |
| 捕获与归档工具 | ArchiveBox、Karakeep、Memos | 保存网页、书签、短笔记和外部资料 | 降低资料丢失，增强快速捕获 | KeyPulse 可以做 timeline/glue，把保存的资料和当时的工作上下文连起来。 |
| 面向 agent 的 Markdown 记忆层 | Basic Memory、Remember.md、IWE、Atomic | 用 Markdown 文件承载记忆，通过 MCP/CLI 给 AI 工具读写 | 记忆持久、可检查、可编辑、可跨工具复用 | 这是最贴近 KeyPulse 下一阶段的方向。KeyPulse 应该成为这个记忆层的 capture + compile 引擎。 |

## 项目笔记

### Logseq

Logseq 是开源知识管理和协作平台，强调本地优先、outline、block、backlink 和 graph 工作流。

- 来源：[logseq/logseq](https://github.com/logseq/logseq)
- 价值：结构化思考和 backlink resurfacing。
- 交付方式：用户主动写笔记和维护图谱。
- 对 KeyPulse 的启发：Logseq 偏主动记录；KeyPulse 可以补被动工作上下文和每日 synthesis。

### SiYuan

SiYuan 是开源个人知识管理系统，支持块级引用、Markdown-like 写作和本地/自托管部署。

- 来源：[siyuan-note/siyuan](https://github.com/siyuan-note/siyuan)
- 价值：强结构化笔记和长期知识组织。
- 交付方式：完整 app、块数据库和编辑器体验。
- 对 KeyPulse 的启发：SiYuan 是 destination/editor；KeyPulse 应该聚焦 source capture、normalization 和 export。

### AFFiNE

AFFiNE 是开源 workspace，把 docs、whiteboard/canvas、database-like organization 放在一起，强调 local-first 和 privacy-first。

- 来源：[toeverything/AFFiNE](https://github.com/toeverything/AFFiNE)
- 价值：灵活的文档、画布、结构化数据一体化 workspace。
- 交付方式：完整生产力套件，自托管和云端选项。
- 对 KeyPulse 的启发：不要和它拼 workspace UX，应该做集成而不是替代。

### TriliumNext Notes

TriliumNext 是面向大型个人知识库的层级笔记应用。

- 来源：[TriliumNext/Notes](https://github.com/TriliumNext/Notes)
- 价值：深层级组织、rich notes、脚本能力、自托管。
- 交付方式：note app 和 server。
- 对 KeyPulse 的启发：适合作为长期知识出口之一，但它不是 capture engine。

### Dendron

Dendron 是 Markdown-first 的开源知识库，基于 VS Code 和层级 schema。

- 来源：[dendronhq/dendron](https://github.com/dendronhq/dendron)
- 价值：对开发者友好的 Markdown 知识组织。
- 交付方式：编辑器扩展和文件笔记。
- 对 KeyPulse 的启发：验证了技术用户对 Markdown 文件流的接受度。

### Khoj

Khoj 是开源个人 AI 应用，可搜索并聊天式访问笔记、文档、图片等个人资料，并有多个笔记工作流集成。

- 来源：[khoj-ai/khoj](https://github.com/khoj-ai/khoj)
- 价值：个人知识上的 AI assistant。
- 交付方式：在已有内容上做 RAG/chat。
- 对 KeyPulse 的启发：Khoj 索引语料；KeyPulse 可以从工作活动中创建语料。

### AnythingLLM

AnythingLLM 是开源的一体化 AI 应用，支持文档、agents、本地或托管模型 provider。

- 来源：[Mintplex-Labs/anything-llm](https://github.com/Mintplex-Labs/anything-llm)
- 价值：通用、provider-flexible 的 RAG 和 agent 体验。
- 交付方式：workspace-based document upload/index/chat。
- 对 KeyPulse 的启发：泛文档聊天已经很拥挤。KeyPulse 要用 timeline、provenance、privacy-safe capture 做差异化。

### PrivateGPT

PrivateGPT 是开源项目，重点是本地/私有地对文档提问。

- 来源：[zylon-ai/private-gpt](https://github.com/zylon-ai/private-gpt)
- 价值：私有文档问答。
- 交付方式：本地 ingestion 和本地 inference 选项。
- 对 KeyPulse 的启发：隐私只是基础门槛。差异化应该来自自动、可信的知识生成，而不是“本地 chat”。

### Open WebUI

Open WebUI 是开源 LLM 界面，带知识库/RAG 能力，支持本地和托管模型。

- 来源：[open-webui/open-webui](https://github.com/open-webui/open-webui)
- 价值：被广泛使用的本地/托管模型 UI。
- 交付方式：模型 UI 加 knowledge base integration。
- 对 KeyPulse 的启发：它可以作为集成面，而不是 KeyPulse 的直接替代品。

### Reor

Reor 是开源 AI 个人知识管理 app，面向本地 Markdown 笔记和语义搜索。

- 来源：[reorproject/reor](https://github.com/reorproject/reor)
- 价值：Markdown 笔记上的私有 AI。
- 交付方式：本地 app、semantic search、LLM assistance。
- 对 KeyPulse 的启发：它是 Markdown-native AI 的参考；KeyPulse 需要在被动捕获和每日编译上做强。

### Basic Memory

Basic Memory 是给 LLM 用的本地优先知识系统，用 Markdown 文件作为 source of truth，SQLite 作为派生索引，并通过 MCP tools 让 assistant 读写和搜索知识图谱。

- 来源：[basicmachines-co/basic-memory](https://github.com/basicmachines-co/basic-memory)，[Basic Memory how it works](https://www.mintlify.com/basicmachines-co/basic-memory/concepts/how-it-works)
- 价值：持久、可检查、可编辑的 AI memory。
- 交付方式：Markdown notes、semantic graph extraction、本地 SQLite indexing、MCP server。
- 对 KeyPulse 的启发：Basic Memory 从对话/笔记开始；KeyPulse 可以提供经过隐私过滤的工作证据和每日/项目记忆更新。

### Remember.md

Remember.md 面向 Claude/OpenClaw 类 agent session 的本地 Markdown memory，通过 hooks/session capture 把有用上下文回收到 second brain。

- 来源：[remember-md/remember](https://github.com/remember-md/remember)，[remember.md](https://remember.md/)
- 价值：没有不透明云存储的持久 agent memory。
- 交付方式：本地 Markdown、deterministic capture/routing、Obsidian-compatible links。
- 对 KeyPulse 的启发：它对“工具使用历史如何变成长期记忆”很有参考价值；KeyPulse 的 OS/app/browser 采集可以提供更宽的证据面。

### IWE

IWE 是面向 AI agents 的本地优先 Markdown knowledge graph / memory system，通过 CLI/MCP 风格工作流暴露能力。

- 来源：[iwe-org/iwe](https://github.com/iwe-org/iwe)，[IWE docs](https://iwe.md/docs/getting-started/installation/)
- 价值：精确、可 refactor、file-based 的 agent memory。
- 交付方式：Markdown 目录作为 source of truth、graph operations、command/tool interface。
- 对 KeyPulse 的启发：长期层应该是文件加显式图谱操作，而不是隐藏 embedding。

### Atomic

Atomic 是自托管个人知识库，把 Markdown notes 变成 semantic graph，支持 SQLite/sqlite-vec 搜索、agentic chat、wiki synthesis 和 MCP server。

- 来源：[kenforthewin/atomic](https://github.com/kenforthewin/atomic)
- 价值：Markdown 上的 AI-augmented knowledge graph。
- 交付方式：Markdown ingestion、vector search、server/API/MCP layer。
- 对 KeyPulse 的启发：Markdown + semantic indexing 是可行组合。KeyPulse 的差异化应该是 capture-to-knowledge compilation。

### RAGFlow

RAGFlow 是开源 RAG engine，重点在文档解析、检索和回答生成。

- 来源：[infiniflow/ragflow](https://github.com/infiniflow/ragflow)
- 价值：质量更高、偏企业级的文档 RAG pipeline。
- 交付方式：ingestion、parsing、retrieval、chat。
- 对 KeyPulse 的启发：可以参考检索质量设计，但 KeyPulse 不应该变成通用 RAG 平台。

### OpenRecall

OpenRecall 是开源 lifelog 项目，定位为本地优先、隐私友好的 Rewind 类替代方案。

- 来源：[openrecall/openrecall](https://github.com/openrecall/openrecall)，[OpenRecall site](https://openrecall.github.io/)
- 价值：自动回忆屏幕历史。
- 交付方式：定期截图、OCR、本地搜索。
- 对 KeyPulse 的启发：证明被动捕获有需求，也暴露隐私风险。KeyPulse 应该偏选择性 evidence 和 redaction。

### screenpipe

screenpipe 是开源项目，用于记录和索引屏幕/音频活动，服务本地 AI 工作流。

- 来源：[screenpipe/screenpipe](https://github.com/screenpipe/screenpipe)，[screenpipe FAQ](https://screenpipe-screenpipe.mintlify.app/resources/faq)
- 价值：给 AI agents 提供丰富本地上下文。
- 交付方式：连续本地 capture、OCR/transcription、developer APIs。
- 对 KeyPulse 的启发：它更像 developer-facing local context engine。KeyPulse 可以用 human-readable knowledge outputs 和 Obsidian 工作流区分。

### Memex

Memex 是移动端个人 life recording 项目，支持文本、照片、语音等输入。

- 来源：[memex-lab/memex](https://github.com/memex-lab/memex)
- 价值：移动端随身记录和回顾。
- 交付方式：filesystem + SQLite、生物识别锁、本地/设备存储。
- 对 KeyPulse 的启发：如果未来做移动端或随身采集，它的 timeline 和数据模型值得参考。

### ArchiveBox

ArchiveBox 是开源自托管互联网归档工具，用于保存网页和链接。

- 来源：[ArchiveBox/ArchiveBox](https://github.com/ArchiveBox/ArchiveBox)
- 价值：长期保存外部 reference。
- 交付方式：网页快照和 metadata。
- 对 KeyPulse 的启发：归档网页可以成为 work timeline 里的证据节点。

### Karakeep

Karakeep 是开源自托管书签/read-it-later 应用，带 AI 辅助 tagging 和全文搜索。

- 来源：[karakeep-app/karakeep](https://github.com/karakeep-app/karakeep)
- 价值：低摩擦保存链接、笔记和媒体，并组织它们。
- 交付方式：browser/app capture、tagging、search。
- 对 KeyPulse 的启发：quick capture 是互补能力。KeyPulse 应该把 saved items 和当时工作上下文连起来。

### Memos

Memos 是开源、自托管、轻量 memo/note 服务。

- 来源：[usememos/memos](https://github.com/usememos/memos)
- 价值：快速个人记录和轻量发布。
- 交付方式：短笔记、tags、timeline-like interface。
- 对 KeyPulse 的启发：timeline/memo UX 是有效形态。KeyPulse 可以从工作上下文自动生成 draft memos。

## 产品方向矩阵

| 方向 | 竞品压力 | 与 KeyPulse 的匹配度 | 建议 |
| --- | --- | --- | --- |
| 完整 PKM 编辑器 | Logseq、SiYuan、AFFiNE、TriliumNext | 低 | 避免。UI 面太大，差异化弱。 |
| 泛 RAG / 文档聊天 | Khoj、AnythingLLM、PrivateGPT、Open WebUI、RAGFlow | 中 | 可以导出或集成，但不要作为主定位。 |
| always-on lifelog | OpenRecall、screenpipe、Rewind 类产品 | 中 | 借鉴检索思路，但默认不要持续截图/录音，隐私风险太高。 |
| 面向 agent 的 Markdown 记忆层 | Basic Memory、IWE、Remember.md、Atomic | 高 | 最值得押注。KeyPulse 可以成为这类记忆层的 capture + compilation engine。 |
| Obsidian-first 工作记忆 | Obsidian 生态和 Markdown-first 工具 | 高 | 最适合第一阶段切入。UX 简单，输出可迁移。 |

最高信号方向是：Obsidian/Markdown-first 的工作记忆编译器，后续可选 MCP access。产品应该从本地活动创建可审计 Markdown memory，再把被用户批准的 memory 暴露给 AI 工具。

## 产品建议

### 1. 先修信任合同

扩产品前，KeyPulse 需要让隐私行为和 README 承诺一致：

- 让 `keyboard_chunk.store_text = false` 真的阻止键入文本落盘。
- 脱敏 `metadata_json`，不能只脱敏 event `text`。
- 实现 private/incognito browser 排除，或者删除该承诺。
- `local-first` 默认 fail closed，除非用户显式打开 cloud fallback。
- 修复 maintenance scrub 命令，让用户能真正清理已存数据。
- ignore 或移走可能含敏感配置的 backup config 文件。

原因：RAG 功能可以被竞品复制，但隐私信任一旦破坏，本地优先定位就站不住。

### 2. 定位成“工作记忆编译器”，不是笔记应用

建议定位：

> KeyPulse 把本地工作活动转成私有、可 review、可追溯来源的 Markdown 记忆，供 Obsidian 和 AI assistants 使用。

这个定位能避开饱和的编辑器赛道，也能解释为什么它应该和 Obsidian、Logseq、AFFiNE、SiYuan 共存。

### 3. 建一条可审计的知识编译管线

建议管线：

- Capture：采集 app/browser/window/clipboard/accessibility events，并在边界做强脱敏和来源标记。
- Normalize：合并 fragments、去重、按 task/project/topic 分类。
- Review：产出每日 candidate facts 和 candidate decisions，让用户接受、编辑或丢弃。
- Compile：把已接受内容提升成 Markdown 页面，如项目、人物、主题、重复工作流、未解决问题。
- Retrieve：回答问题时引用 notes 和 source events。

这比纯 vector database 更接近 Karpathy 的 wiki 思路。

### 4. 做出差异化价值

真正有价值的用例不是“搜索我的笔记”，开源工具已经很多。更强的切入点是：

- “我今天实际做了什么，哪些应该沉淀为长期知识？”
- “这周我做了哪些决策，证据是什么？”
- “我反复遇到的 debugging / writing / research 模式是什么？”
- “哪些 tabs、docs、messages 影响了这个项目笔记？”
- “开会、事故复盘或提交代码前，能不能重建上下文？”

### 5. 集成优先，不要替代

高杠杆集成：

- Obsidian vault export 继续作为主出口。
- Markdown 输出要足够干净，让 Dendron/Reor/Khoj 能直接索引。
- 可选 JSONL/SQLite event export，方便 Open WebUI、AnythingLLM 或自定义 RAG 使用。
- ArchiveBox/Karakeep 类 saved links 可以作为 evidence 被引用。

### 6. 路线图建议

Phase 0：信任加固

- 修完评审里发现的隐私问题。
- 增加回归测试：不保存 raw typed text、metadata redaction、private browser exclusion、local-only model behavior。

Phase 1：Evidence notes

- 每日 Markdown 明确分区：`Observed`、`Decisions`、`Questions`、`Artifacts`、`Promote to Knowledge`。
- 每条生成 claim 都带 event ID 或 source link。

Phase 2：Knowledge compiler

- 把已 review 的每日笔记提升为 project/topic/person pages。
- 用 append-only history 记录 AI 改动，方便用户审计。

Phase 3：Local answer layer

- 先对 curated Markdown 做 local-first search/Q&A。
- 只有用户明确要求 forensic recall 时，才回查 raw events。

Phase 4：Agent context

- 暴露只读 MCP/resource interface，给 AI 工具读取 approved knowledge pages 和 summaries。
- 默认不要暴露 raw event streams。

Phase 5：Controlled write-back

- 允许 agent 提议 memory updates，但先进入 review queue。
- raw capture、accepted knowledge、agent-written memory 分 namespace。
- 增加 stale-memory detection：决策可以被 supersede，证据可能失效，summary 要有 last-reviewed timestamp。

## 明确不要做

- 不要做完整 editor/canvas/whiteboard。
- 不要默认 always-on 截屏或录音。
- 不要把泛 RAG 当主产品。
- 不要默认把 raw captured text 发给云模型。
- 不要生成不可追溯来源的总结；长期 claim 必须有 provenance。

## 待确认问题

- 第一目标用户是开发者、创始人/运营者、研究者，还是普通知识工作者？
- KeyPulse 应该只 Obsidian-first，还是 Markdown-first 且以 Obsidian 为旗舰集成？
- 最小可信采集范围是什么：只采 browser/app/window，还是包含 accessibility text？
- AI 生成的知识必须手动 opt-in promotion，还是可以自动生成每日笔记？
- 商业模式更适合 local app、open-core、paid sync、paid model gateway，还是企业隐私/合规？
