# 贡献指南

感谢你对 KeyPulse 的关注！我们欢迎任何形式的贡献。

## 🤝 如何贡献

### 报告 Bug

如果你发现了 bug，请：

1. 检查 [Issues](https://github.com/Longfellow1/keypulse/issues) 是否已有相同问题
2. 如果没有，创建新 Issue，包含：
   - 清晰的标题
   - 详细的问题描述
   - 复现步骤
   - 预期行为 vs 实际行为
   - 系统环境（macOS 版本、Swift 版本）
   - 相关日志或截图

### 提出新功能

如果你有好的想法：

1. 先创建 Issue 讨论
2. 说明功能的使用场景和价值
3. 等待维护者反馈
4. 获得认可后再开始开发

### 提交代码

1. **Fork 项目**
   ```bash
   # 在 GitHub 上点击 Fork 按钮
   git clone https://github.com/YOUR_USERNAME/keypulse.git
   cd keypulse
   ```

2. **创建分支**
   ```bash
   git checkout -b feature/your-feature-name
   # 或
   git checkout -b fix/your-bug-fix
   ```

3. **开发和测试**
   ```bash
   swift build
   swift test  # 如果有测试
   ```

4. **提交代码**
   ```bash
   git add .
   git commit -m "feat: 添加新功能描述"
   # 或
   git commit -m "fix: 修复某个问题"
   ```

5. **推送并创建 PR**
   ```bash
   git push origin feature/your-feature-name
   # 然后在 GitHub 上创建 Pull Request
   ```

## 📝 代码规范

### Commit Message 格式

使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

```
<type>: <description>

[optional body]

[optional footer]
```

**Type 类型：**
- `feat`: 新功能
- `fix`: Bug 修复
- `docs`: 文档更新
- `style`: 代码格式（不影响功能）
- `refactor`: 重构
- `perf`: 性能优化
- `test`: 测试相关
- `chore`: 构建/工具相关

**示例：**
```
feat: 添加自动导出 PDF 功能

- 支持导出日报为 PDF 格式
- 添加自定义模板选项
- 更新文档

Closes #123
```

### Swift 代码规范

1. **命名规范**
   - 类名：大驼峰（PascalCase）
   - 函数/变量：小驼峰（camelCase）
   - 常量：小驼峰
   - 枚举：大驼峰

2. **注释**
   - 公共 API 必须有文档注释
   - 复杂逻辑需要解释性注释
   - 使用 `// MARK: -` 分隔代码段

3. **代码风格**
   - 缩进：4 个空格
   - 行宽：建议不超过 120 字符
   - 使用 Swift 标准库优先

**示例：**
```swift
/// 智能脱敏处理器
/// 
/// 根据输入上下文自动选择脱敏策略，保护用户隐私
class TextDesensitizer {
    // MARK: - Properties
    
    private let keywords: Set<String>
    
    // MARK: - Public Methods
    
    /// 脱敏文本内容
    /// - Parameters:
    ///   - text: 原始文本
    ///   - context: 输入上下文
    /// - Returns: 脱敏后的文本
    func desensitize(_ text: String, context: InputContext) -> DesensitizedText {
        // Implementation
    }
}
```

## 🧪 测试

目前项目还没有完整的测试覆盖，这是一个很好的贡献方向！

如果你想添加测试：

1. 在项目根目录创建 `Tests/` 目录
2. 使用 XCTest 框架
3. 确保测试可以通过 `swift test` 运行

## 📚 文档

文档同样重要！你可以：

- 改进 README
- 添加使用示例
- 翻译文档（英文版）
- 完善 API 文档
- 录制使用视频

## 🐛 调试技巧

### 查看日志

```bash
# 启动时会输出日志
keypulse start

# 查看数据库
sqlite3 ~/.keypulse/data.db
```

### 常见问题

1. **编译失败**
   - 检查 Xcode 版本（需要 13.0+）
   - 检查 Swift 版本（需要 5.5+）
   - 清理构建：`swift package clean`

2. **权限问题**
   - 确保已授予辅助功能权限
   - 系统设置 → 隐私与安全性 → 辅助功能

3. **数据库问题**
   - 删除数据库重新开始：`rm -rf ~/.keypulse/`

## 🎯 优先级任务

当前最需要的贡献：

- [ ] 添加单元测试
- [ ] 英文文档翻译
- [ ] 优化脱敏算法
- [ ] 支持更多编程语言关键词
- [ ] 添加配置文件支持
- [ ] 改进错误处理
- [ ] 性能优化

## 💬 交流

- GitHub Issues: 技术问题和 Bug 报告
- GitHub Discussions: 功能讨论和想法交流
- Email: Harland5588@outlook.com

## 📜 行为准则

- 尊重他人
- 保持友善和专业
- 接受建设性批评
- 关注项目目标

## 🙏 致谢

感谢所有贡献者！你们的每一个 PR、Issue、建议都让 KeyPulse 变得更好。

---

**再次感谢你的贡献！** 🎉
