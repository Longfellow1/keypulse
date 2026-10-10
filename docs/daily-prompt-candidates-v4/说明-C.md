📍 Asia/Shanghai

# 2026-05-06

## 今日要点

今天最花精力的是 KeyPulse 的 HUD 功能重构——为了让信号源准确读取日报主题，你在 Terminal、Claude、代码编辑器和 Obsidian 之间反复切换了 5 次（跨 4 个应用），最终把「今日最新」模块改写成解析 daily.md 里的「## 今日主线」H3 标题，还顺手把输入框改回了 inline 模式。CorpusFlow 则做了个关键减法：放弃完整 SFT 工厂定位，只聚焦批量 Query 生成，把多轮上下文、精调对接这些都往后排了。

## 今天做的事

### KeyPulse

#### HUD 信号源与输入交互优化
凌晨 00:05 提交代码，修改 4 个文件（176 新增/98 删除），核心改动是「signals 改吃日报「今日主线」主题 + 今日的事儿改回 inline 输入」。你在修改说明里提到，新逻辑会解析 daily.md 里「## 今日主线」下的 `### XXX` 标题，点击还能跳转到 Obsidian 对应段落。这个过程中反复回到 Terminal 执行 git 操作、在 Claude 里讨论实现细节，跨应用切换达 4 次。

#### 日报主题解析功能开发
写了 `_parse_daily_topics` 函数来提取日报主题，配套 `test_parse_daily_topics_extracts_h3_under_main_section` 测试用例确保逻辑正确。同时实现 `_obsidian_open_url` 函数，支持从 HUD 直接打开 Obsidian 对应笔记段落。

#### HUD 后台行为确认
验证发现 HUD 退出后状态栏图标会消失，但后台采集 daemon 会继续运行，符合「前台轻量展示、后台持续工作」的设计预期。

### CorpusFlow

#### 范围收缩决策
明确核心用户是测试团队、数据 PM 和算法工程师，短期最强需求是批量上传 100-200 seed、高质量 query 生成、去重/训练测试集隔离。最终决定 DE-SCOPE：先打穿批量 Query 生成，多轮上下文只做开关后续补模板，精调/LLaMA-Factory 仅导出契约，代码生成、Bad Case 诊断等功能全部延后。文档里写着「当前应定义为可控的批量评测 query 与数据增强工具，不是完整 sft 工厂或评测平台」。

#### 批量生成逻辑问题排查
发现微调+多轮功能逻辑错误：按 LLaMA-Factory 规范，system 是可选，但 instruction（提示词）必填、input 是当前 query、output 是最终输出，而现有代码把字段关系搞混了，导致「无法生成指令微调+多轮内容」，需要重新梳理。

### GitNexus

今天提交 3 个维护性修复：扩展 worker pool 重试超时防止 CI 抖动（#1323 #1354）、添加 Docker 专用健康检查端点（#1147 #1355）、使用 ipKeyGenerator 处理 IPv6 子网归一化（#1360 #1374），都是小范围优化。

### 跨项目 / 杂项

KeyPulse 项目文档评审时出现跨实体冲突（needs_review=true），具体待确认。此外，你还看了奇瑞股价波动分析、望京租房信息（京旺家园7区一室一厅 4100元/月）和 BOSS 直聘简历模板，中途提了句「我不想再用 ragflow 了 / 全面转线下吧」，但期望 rag 项目做完后能模块化复用。

## 明日的锚点

> 明天我想：______
>
> _写一句话留给明天的自己_