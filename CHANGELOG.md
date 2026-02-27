# 更新日志

所有重要的项目变更都会记录在这个文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [2.0.0] - 2026-02-27

### 🎉 重大升级：智能脱敏技术

这是一个重大版本升级，引入了智能脱敏技术，在保护隐私的前提下记录有价值的工作内容。

### 新增 (Added)

- ✨ **智能脱敏引擎** (`TextDesensitizer.swift`)
  - 根据应用类型自动选择脱敏策略
  - 保留编程关键字和技术术语
  - 提取动作词和关键词
  - 自动过滤敏感内容
  
- ✨ **键盘输入捕获** (`KeystrokeCounter.swift`)
  - 使用 CGEvent 捕获键盘输入
  - 实时脱敏处理
  - 按应用分组缓冲
  - 黑名单保护敏感应用
  
- ✨ **项目名智能提取** (`ProjectExtractor.swift`)
  - 从窗口标题自动提取项目名
  - 支持多种应用的解析规则
  - 智能识别工作上下文
  
- ✨ **增强的日报生成** (`ReportGenerator.swift`)
  - 显示脱敏后的工作内容
  - 按项目分组并显示关键词
  - 按内容类型统计时长
  - 更详细的工作描述

- 📝 **完整的开源文档**
  - LICENSE (MIT)
  - CONTRIBUTING.md
  - CHANGELOG.md
  - 详细的 README

### 改进 (Changed)

- 🔄 **数据库扩展**
  - 新增 `desensitized_text` 字段
  - 新增 `keywords` 字段
  - 新增 `content_category` 字段
  
- 🔄 **ActivityMonitor 重构**
  - 集成键盘输入捕获
  - 优化数据保存逻辑
  - 改进日志输出

- 📊 **日报格式优化**
  - 显示工作内容摘要
  - 显示提取的关键词
  - 按活动类型分类统计

### 安全 (Security)

- 🔒 **隐私保护增强**
  - 智能脱敏，不存储原始输入
  - 黑名单保护敏感应用
  - 敏感内容自动检测和过滤
  - 本地存储，不上传云端

### 性能 (Performance)

- ⚡ 优化内存使用
- ⚡ 减少数据库写入频率
- ⚡ 改进事件处理效率

---

## [1.0.0] - 2026-02-25

### 初始版本

- ✅ 基础活动监控
- ✅ 应用切换记录
- ✅ 窗口标题获取
- ✅ 简单的时间统计
- ✅ CLI 命令支持

### 功能

- `keypulse start` - 启动监控
- `keypulse stop` - 停止监控
- `keypulse status` - 查看状态
- `keypulse timeline` - 查看时间线
- `keypulse summary` - 查看摘要

### 限制

- ❌ 只记录应用和窗口标题
- ❌ 没有工作内容记录
- ❌ 日报价值有限

---

## 版本说明

### 版本号规则

- **主版本号 (Major)**: 不兼容的 API 变更
- **次版本号 (Minor)**: 向下兼容的功能新增
- **修订号 (Patch)**: 向下兼容的问题修正

### 标签说明

- `Added` - 新增功能
- `Changed` - 功能变更
- `Deprecated` - 即将废弃的功能
- `Removed` - 已移除的功能
- `Fixed` - Bug 修复
- `Security` - 安全相关

---

## 路线图

### v2.1.0 (计划中)

- [ ] 配置文件支持
- [ ] 自定义脱敏规则
- [ ] 导出 PDF 格式
- [ ] 英文文档

### v2.2.0 (计划中)

- [ ] 数据可视化
- [ ] 周报/月报生成
- [ ] 习惯分析建议
- [ ] 更多编程语言支持

### v3.0.0 (未来)

- [ ] 云同步（可选）
- [ ] 团队版
- [ ] AI 智能总结
- [ ] 移动端查看

---

[2.0.0]: https://github.com/Longfellow1/keypulse/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/Longfellow1/keypulse/releases/tag/v1.0.0
