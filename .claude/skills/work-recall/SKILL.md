---
name: work-recall
description: |
  查询用户本机的工作上下文记忆。当用户询问以下类型问题时自动触发：
  "我刚刚看过什么"、"我最近复制了什么"、"我今天在做什么"、
  "我昨天在研究什么"、"我前几天看过 X 相关的什么"、"帮我接续上下文"。
  需要 keypulse daemon 在后台运行（keypulse start）。
allow-tools: Bash(keypulse *)
---

# Work Recall — 本地工作记忆查询

!`keypulse recall "$ARGUMENTS" --since 7d --limit 5 2>/dev/null || echo "[错误] KeyPulse 未运行，请先执行: keypulse start"`

---

基于以上本地上下文，回答用户的问题：**$ARGUMENTS**

回答要求：
- 直接作答，不要复述原始数据格式
- 时间用自然语言表达（"今天下午"、"昨天上午"）
- 相关条目按时间倒序，最多列 5 条
- 内容超过 2 句话则精简
- 如果没有找到，直接说"未找到相关记录"，不要猜测
