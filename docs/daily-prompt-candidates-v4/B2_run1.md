📍 Asia/Shanghai

# 2026-05-06

## 今日要点

你今天花了20小时在KeyPulse的HUD功能上，反复在Terminal、Claude、代码编辑器和浏览器间切换5次，最终提交了「signals改吃日报主题+今日的事儿改回inline输入」的代码。更关键的判断是给CorpusFlow做了范围收缩：放弃精调/LLaMA-Factory主定位，先打穿批量Query生成。

## 今天做的事

### KeyPulse HUD功能优化

凌晨00:05你提交了代码，修改包括「signals改吃日报「今日主线」主题」——解析daily.md里「## 今日主线」下的H3标题，以及「今日的事儿改回inline输入」。为此写了_parse_daily_topics函数和对应的测试用例test_parse_daily_topics_extracts_h3_under_main_section，还调整了_obsidian_open_url函数支持标题锚点跳转。因为这个功能你在多个应用间反复切换了4次，当天回访了5次，直到21:02还在补充「采集异常时多一个『重启daemon』按钮」的需求。

### KeyPulse 后台运行逻辑确认

15:44你确认HUD退出后状态栏图标会消失，但后台采集daemon会继续运行，这解决了之前担心的进程残留问题。

### CorpusFlow 产品范围收缩决策

9:26你明确核心用户是测试团队、数据PM和算法工程师，短期最强需求是批量上传100-200 seed、高质量query生成、参数泛化等。最终判断「DE-SCOPE / 降级做」：先打穿批量Query生成，多轮上下文构造作为后续轻量模板，精调/LLaMA-Factory仅保留导出契约，代码生成、Bad Case诊断等功能全部后置。这个决策被记录在brief-corpusflow文档里，定义产品为「可控的批量评测query与数据增强工具」，而非完整SFT工厂或评测平台。

### CorpusFlow MVP开发选择

15:32你查看了GitHub上的CorpusFlow项目描述，15:36在Claude中讨论「MVP开发选择：LlamaFactory vs其他方案」，但未明确结论。

### GitNexus 代码提交

今天GitNexus有三次代码提交：02:15修复CI超时问题，04:40添加容器健康检查端点，21:07优化IPv6子网归一化。

### 杂项

9:46浏览奇瑞股价波动原因分析，17:25访问Apple问题报告页面，19:54查看望京租房信息，22:15浏览BOSS直聘简历模板。

## 今天的卡壳

KeyPulse的HUD功能优化反复修改了5次，跨4个应用切换，从凌晨00:05写到21:07才补充完最后一个需求点，中间多次回到代码编辑器调整解析逻辑。

## 明日的锚点

> 明天我想：______
>
> _写一句话留给明天的自己_