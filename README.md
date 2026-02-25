# 🔑 KeyPulse

> **Low-power activity monitor for macOS**
> 
> 低功耗的 macOS 活动监控工具，帮你记录工作轨迹，生成时间线报告。

[![Swift](https://img.shields.io/badge/Swift-5.5-orange.svg)](https://swift.org)
[![Platform](https://img.shields.io/badge/Platform-macOS-blue.svg)](https://developer.apple.com/macos)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## ✨ 特性

- 🪶 **低功耗** - CPU < 0.5%，内存 < 50MB，电池影响 < 1%/小时
- 🔒 **隐私优先** - 仅记录应用名/窗口标题，不记录键盘输入
- 📊 **时间线报告** - 自动生成每日/每周工作摘要
- 🛠️ **CLI 工具** - 简单易用的命令行界面
- 📦 **开源免费** - MIT 许可证，欢迎贡献

---

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

# 3. 启动监听器
.build/release/keypulse start

# 4. 授权辅助功能权限
# 系统设置 → 隐私与安全性 → 辅助功能 → 添加 keypulse
```

### 使用

```bash
# 查看今日时间线
.keypulse timeline

# 查看今日摘要
.keypulse summary

# 查看系统状态
.keypulse status

# 查看统计数据
.keypulse stats --today

# 导出数据
.keypulse export --format=md
.keypulse export --format=json

# 停止监听器
.keypulse stop
```

---

## 📊 输出示例

### 时间线

```
📊 2026-02-25 时间线

09:00-11:00 VSCode (2h)
  ├─ design-system.md (1h)
  └─ index.ts (1h)

11:00-12:00 Safari (1h)
  ├─ github.com (30m)
  └─ stackoverflow.com (30m)

14:00-14:30 Terminal (30m)
  └─ git commit, npm install
```

### 系统状态

```
🔋 KeyPulse 资源占用
CPU: 0.3%
内存：32MB
磁盘 IO: 0.5MB/小时
电池影响：< 1%/小时
```

---

## 🔒 隐私与安全

### 记录的内容

- ✅ 应用名称（如 VSCode, Safari）
- ✅ 窗口标题（如 design-system.md）
- ✅ 活动时长
- ✅ 浏览器域名（如 github.com）

### 不记录的内容

- ❌ 键盘输入（密码、聊天内容）
- ❌ 屏幕截图
- ❌ 文件内容
- ❌ 剪贴板内容

### 数据存储

- 所有数据本地存储（`~/.keypulse/`）
- SQLite 加密存储（SQLCipher）
- 可设置自动删除（默认 30 天）
- 一键清空所有数据

---

## ⚙️ 配置

配置文件位于 `~/.keypulse/config.json`：

```json
{
  "privacy": {
    "collect_window_title": true,
    "collect_url": false,
    "retention_days": 30
  },
  "output": {
    "daily_report_time": "18:00",
    "feishu_webhook": ""
  },
  "blacklist": {
    "apps": ["1Password", "Keychain Access"],
    "domains": ["gmail.com", "github.com/settings"]
  }
}
```

---

## 🏗️ 架构

```
┌─────────────────────────────────────────┐
│          采集层（低功耗）                │
├─────────────────────────────────────────┤
│  L1: 事件驱动（NSWorkspace 通知）        │
│  L2: 低频轮询（10 秒兜底）               │
│  L3: 手动打点（Karabiner 快捷键）        │
└─────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────┐
│          处理层（会话化合并）            │
├─────────────────────────────────────────┤
│  快速闪切过滤（< 3 秒）                   │
│  Idle 超时断开（5 分钟）                  │
│  批量写入（累积 10 条）                   │
└─────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────┐
│          输出层（CLI 工具）              │
├─────────────────────────────────────────┤
│  timeline / summary / status / export   │
└─────────────────────────────────────────┘
```

---

## 📈 性能

| 场景 | CPU | 内存 | 电池 |
|------|-----|------|------|
| **空闲** | < 0.1% | 20MB | 无感 |
| **正常办公** | < 0.5% | 30MB | < 1%/小时 |
| **重度使用** | < 1% | 50MB | < 2%/小时 |

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

### 开发环境设置

```bash
git clone https://github.com/Longfellow1/keypulse.git
cd keypulse
swift build
swift test
```

### 代码规范

- 遵循 Swift 官方代码规范
- 所有公共 API 需要有文档注释
- 新功能需要包含单元测试

---

## 📄 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件

---

## 🙏 致谢

- [SQLite.swift](https://github.com/stephencelis/SQLite.swift) - SQLite  Swift 接口
- [Karabiner-Elements](https://karabiner-elements.pqrs.org/) - macOS 键盘定制工具

---

## 📬 联系方式

- GitHub Issues: https://github.com/Longfellow1/keypulse/issues
- Email: Harland5588@outlook.com


---

