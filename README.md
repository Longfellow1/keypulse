# 🔑 KeyPulse

**30 秒生成工作日报的命令行工具**

低功耗的 macOS 活动监控工具，自动记录工作轨迹，**智能脱敏输入内容**，一键生成专业日报。

[![Swift](https://img.shields.io/badge/Swift-5.5+-orange.svg)](https://swift.org)
[![macOS](https://img.shields.io/badge/macOS-12.0+-blue.svg)](https://developer.apple.com/macos)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## ✨ 核心价值

- 🎯 **解决痛点** - 不记得今天干了什么？自动帮你记录
- ⚡ **30 秒生成** - 一个命令，生成可直接使用的工作日报
- 🔒 **智能脱敏** - 记录工作内容，保护隐私信息
- 🪶 **低功耗** - CPU < 1%，内存 < 50MB，不影响电池续航

## 📊 效果展示

```bash
$ keypulse report
```

**输出：**

```markdown
## 2026-02-27 工作日报

### keypulse 项目（4.5h）
- 09:00-12:00 核心功能开发（VSCode，高强度，1,850 键击，代码）
  内容：func, class, struct, database, API
  **关键词：** Swift, ActivityMonitor, KeystrokeCounter, database

- 14:00-16:30 代码调试（Terminal + VSCode，中强度，680 键击）
  内容：swift build, test, debug
  **关键词：** test, debug, build

### 产品文档（1.5h）
- 13:00-14:30 需求文档编写（飞书，中强度，320 键击，文档）
  内容：修复, 优化, 实现, API, 用户
  **关键词：** 修复, 优化, 用户需求, 功能设计

---
💡 **今日工作统计**

- 总时长：6h
- 总键击：2,850 次
- 活动分布：
  - 代码：4.5h
  - 文档：1.5h
```

✅ **报告已自动复制到剪贴板，可直接粘贴到飞书/钉钉**

## 🔒 智能脱敏技术

### 记录的内容

- ✅ 应用名称（如 VSCode, Safari）
- ✅ 窗口标题（如 keypulse/main.swift）
- ✅ **脱敏后的输入内容**（保留关键词，去除敏感信息）
- ✅ 键击次数
- ✅ 活动时长

### 智能脱敏策略

#### 代码输入
```
实际输入：const username = "zhangsan@company.com"
脱敏记录：const, username（关键词）
```

#### 文档编写
```
实际输入：修复了登录模块的 JWT token 过期问题
脱敏记录：修复, 登录, JWT, token, 过期（关键词）
```

#### 终端命令
```
实际输入：cd /Users/zhangsan/projects/keypulse
脱敏记录：cd /PATH（路径脱敏）
```

#### 聊天内容
```
实际输入：（任何聊天内容）
脱敏记录：[聊天内容]（完全不记录）
```

### 不记录的内容

- ❌ 具体变量值和字符串内容
- ❌ 密码输入（检测到密码框自动过滤）
- ❌ 敏感应用内容（1Password 等）
- ❌ 个人隐私信息

### 黑名单保护

以下敏感应用完全不会被监控：

- 1Password
- Keychain Access
- LastPass
- Bitwarden
- KeePassXC

## 🚀 快速开始

### 系统要求

- macOS 12.0+
- Xcode 13.0+
- Swift 5.5+

### 安装

```bash
# 1. Clone 仓库
git clone https://github.com/Longfellow1/keypulse.git
cd keypulse

# 2. 编译
swift build -c release

# 3. 安装到系统（可选）
sudo cp .build/release/keypulse /usr/local/bin/

# 4. 启动监控
keypulse start

# 5. 授权辅助功能权限
# 系统设置 → 隐私与安全性 → 辅助功能 → 添加 keypulse
```

### 使用

```bash
# 启动后台监控（开机自启动）
keypulse start

# 生成今日工作日报
keypulse report

# 查看运行状态
keypulse status

# 停止监控
keypulse stop

# 清空所有数据
keypulse clear
```

## 🎯 核心功能

### 1. 自动监控（零打扰）

- ✅ 应用切换监控
- ✅ 窗口标题记录
- ✅ **智能输入捕获**（脱敏处理）
- ✅ 工作时长计算

### 2. 智能分组

自动从窗口标题提取项目名：

```
"VSCode - keypulse/main.swift" → 项目：keypulse
"Safari - GitHub PR #123" → 项目：GitHub
"飞书 - 产品需求评审" → 任务：产品需求评审
```

### 3. 工作强度分析

根据键击频率自动判断：

- **高强度**（> 20 次/分钟）- 编码、写作
- **中强度**（5-20 次/分钟）- 调试、阅读
- **低强度**（< 5 次/分钟）- 浏览、思考

### 4. 内容智能分类

根据应用类型自动分类：

- **代码** - 代码编辑器（VSCode, Xcode）
- **文档** - 文档编辑（Pages, Notion）
- **聊天** - 通讯工具（飞书, 钉钉, 微信）
- **命令** - 终端（Terminal, iTerm2）
- **浏览** - 浏览器（Safari, Chrome）

### 5. 一键生成日报

- Markdown 格式，可直接复制
- 自动复制到剪贴板
- 按项目分组，清晰易读
- 包含关键词和工作内容摘要

## 📈 性能表现

| 场景 | CPU | 内存 | 电池影响 |
|------|-----|------|---------|
| 空闲 | < 0.1% | 20MB | 无感 |
| 正常办公 | < 0.5% | 30MB | < 1%/小时 |
| 重度使用 | < 1% | 50MB | < 2%/小时 |

## 🏗️ 技术架构

```
┌─────────────────────────────────────┐
│  后台守护进程（keypulse daemon）      │
├─────────────────────────────────────┤
│  • 监听应用切换（NSWorkspace）        │
│  • 监听键盘事件（CGEvent）            │
│  • 智能脱敏处理（TextDesensitizer）  │
│  • 每 10 秒保存一次数据              │
└─────────────────────────────────────┘
           ↓ 存储
┌─────────────────────────────────────┐
│  SQLite 数据库（~/.keypulse/data.db）│
├─────────────────────────────────────┤
│  activities 表：                     │
│  - timestamp                        │
│  - app_name                         │
│  - window_title                     │
│  - keystroke_count                  │
│  - duration                         │
│  - desensitized_text（脱敏文本）     │
│  - keywords（关键词）                │
│  - content_category（内容类别）      │
└─────────────────────────────────────┘
           ↓ 读取
┌─────────────────────────────────────┐
│  CLI 工具（keypulse report）         │
├─────────────────────────────────────┤
│  1. 读取今日数据                     │
│  2. 智能分组（提取项目名）            │
│  3. 计算工作强度                     │
│  4. 生成 Markdown 报告              │
└─────────────────────────────────────┘
```

## 🎯 适用场景

### 场景 1：每日写日报

下班前运行 `keypulse report`，自动生成今日工作内容，直接复制到飞书/钉钉。

### 场景 2：周报总结

查看本周工作分布，了解时间都花在哪些项目上。

### 场景 3：客户对账

向客户证明工作时长，有详细的时间记录和工作内容摘要。

### 场景 4：自我管理

了解自己的工作习惯，优化时间分配。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

### 开发环境设置

```bash
git clone https://github.com/Longfellow1/keypulse.git
cd keypulse
swift build
swift run keypulse help
```

## 📄 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件

## 📬 联系方式

- GitHub Issues: [https://github.com/Longfellow1/keypulse/issues](https://github.com/Longfellow1/keypulse/issues)
- Email: [Harland5588@outlook.com](mailto:Harland5588@outlook.com)

---

**⭐ 如果这个工具帮到了你，请给个 Star！**
