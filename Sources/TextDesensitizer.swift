import Foundation

/// 文本脱敏器 - 智能脱敏，保留有价值信息
class TextDesensitizer {
    static let shared = TextDesensitizer()
    private init() {}
    
    // 编程语言关键字（保留）
    private let programmingKeywords: Set<String> = [
        // Swift
        "func", "class", "struct", "enum", "protocol", "extension", "import", "let", "var", "if", "else", "for", "while", "return", "guard", "switch", "case",
        // JavaScript/TypeScript
        "function", "const", "async", "await", "export", "import", "interface", "type", "class", "extends",
        // Python
        "def", "class", "import", "from", "return", "if", "else", "for", "while", "try", "except",
        // Common
        "public", "private", "static", "void", "new", "this", "super", "null", "true", "false"
    ]
    
    // 技术术语（保留）
    private let technicalTerms: Set<String> = [
        "API", "REST", "HTTP", "HTTPS", "JSON", "XML", "SQL", "database", "server", "client",
        "JWT", "token", "auth", "login", "logout", "session", "cookie",
        "git", "commit", "push", "pull", "merge", "branch",
        "test", "debug", "build", "deploy", "release",
        "iOS", "Android", "macOS", "Linux", "Windows",
        "Swift", "Python", "JavaScript", "TypeScript", "Java", "Go", "Rust"
    ]
    
    // 动作词（保留）
    private let actionVerbs: Set<String> = [
        "修复", "优化", "重构", "实现", "添加", "删除", "更新", "调试", "测试", "部署",
        "fix", "optimize", "refactor", "implement", "add", "remove", "update", "debug", "test", "deploy",
        "create", "delete", "modify", "improve", "enhance"
    ]
    
    // 敏感词（完全过滤）
    private let sensitivePatterns = [
        "password", "密码", "token", "secret", "key", "credential"
    ]
    
    /// 脱敏文本
    func desensitize(_ text: String, context: InputContext) -> DesensitizedText {
        // 检测敏感内容
        if containsSensitiveContent(text) {
            return DesensitizedText(
                original: text,
                desensitized: "[敏感内容已过滤]",
                keywords: [],
                category: .sensitive
            )
        }
        
        // 根据上下文选择脱敏策略
        switch context {
        case .code:
            return desensitizeCode(text)
        case .document:
            return desensitizeDocument(text)
        case .chat:
            return desensitizeChat(text)
        case .terminal:
            return desensitizeTerminal(text)
        case .browser:
            return desensitizeBrowser(text)
        case .unknown:
            return desensitizeGeneric(text)
        }
    }
    
    // MARK: - 上下文特定脱敏
    
    /// 脱敏代码
    private func desensitizeCode(_ code: String) -> DesensitizedText {
        var desensitized = code
        var keywords: [String] = []
        
        // 提取编程关键字
        let words = code.components(separatedBy: CharacterSet.alphanumerics.inverted)
        for word in words {
            let lower = word.lowercased()
            if programmingKeywords.contains(lower) || technicalTerms.contains(word) {
                keywords.append(word)
            }
        }
        
        // 脱敏变量名（保留结构）
        desensitized = desensitized.replacingOccurrences(
            of: #"[a-zA-Z_][a-zA-Z0-9_]*"#,
            with: "VAR",
            options: .regularExpression
        )
        
        // 脱敏字符串内容
        desensitized = desensitized.replacingOccurrences(
            of: #""[^"]*""#,
            with: "\"STRING\"",
            options: .regularExpression
        )
        
        // 脱敏数字
        desensitized = desensitized.replacingOccurrences(
            of: #"\b\d+\b"#,
            with: "NUM",
            options: .regularExpression
        )
        
        return DesensitizedText(
            original: code,
            desensitized: desensitized,
            keywords: Array(Set(keywords)),
            category: .code
        )
    }
    
    /// 脱敏文档
    private func desensitizeDocument(_ text: String) -> DesensitizedText {
        var keywords: [String] = []
        
        // 提取技术术语和动作词
        let words = text.components(separatedBy: CharacterSet.alphanumerics.inverted)
        for word in words {
            if technicalTerms.contains(word) || actionVerbs.contains(word) {
                keywords.append(word)
            }
        }
        
        // 简化文档内容，只保留关键词
        let summary = keywords.isEmpty ? "[文档内容]" : keywords.joined(separator: ", ")
        
        return DesensitizedText(
            original: text,
            desensitized: summary,
            keywords: Array(Set(keywords)),
            category: .document
        )
    }
    
    /// 脱敏聊天
    private func desensitizeChat(_ text: String) -> DesensitizedText {
        // 聊天内容高度敏感，只记录是否在聊天
        return DesensitizedText(
            original: text,
            desensitized: "[聊天内容]",
            keywords: [],
            category: .chat
        )
    }
    
    /// 脱敏终端命令
    private func desensitizeTerminal(_ command: String) -> DesensitizedText {
        var keywords: [String] = []
        
        // 提取命令关键词
        let words = command.components(separatedBy: .whitespaces)
        if let cmd = words.first {
            keywords.append(cmd)
        }
        
        // 脱敏路径和参数
        var desensitized = command
        desensitized = desensitized.replacingOccurrences(
            of: #"/[^\s]+"#,
            with: "/PATH",
            options: .regularExpression
        )
        
        return DesensitizedText(
            original: command,
            desensitized: desensitized,
            keywords: keywords,
            category: .terminal
        )
    }
    
    /// 脱敏浏览器内容
    private func desensitizeBrowser(_ text: String) -> DesensitizedText {
        // 浏览器输入可能是搜索或表单，高度敏感
        return DesensitizedText(
            original: text,
            desensitized: "[浏览器输入]",
            keywords: [],
            category: .browser
        )
    }
    
    /// 通用脱敏
    private func desensitizeGeneric(_ text: String) -> DesensitizedText {
        var keywords: [String] = []
        
        // 提取所有可能的关键词
        let words = text.components(separatedBy: CharacterSet.alphanumerics.inverted)
        for word in words {
            let lower = word.lowercased()
            if programmingKeywords.contains(lower) || 
               technicalTerms.contains(word) || 
               actionVerbs.contains(word) {
                keywords.append(word)
            }
        }
        
        return DesensitizedText(
            original: text,
            desensitized: keywords.isEmpty ? "[文本内容]" : keywords.joined(separator: ", "),
            keywords: Array(Set(keywords)),
            category: .generic
        )
    }
    
    // MARK: - 辅助方法
    
    /// 检测敏感内容
    private func containsSensitiveContent(_ text: String) -> Bool {
        let lower = text.lowercased()
        return sensitivePatterns.contains { lower.contains($0) }
    }
}

// MARK: - 数据结构

/// 输入上下文
enum InputContext {
    case code        // 代码编辑器
    case document    // 文档编辑
    case chat        // 聊天应用
    case terminal    // 终端命令
    case browser     // 浏览器输入
    case unknown     // 未知
    
    /// 从应用名推断上下文
    static func from(app: String) -> InputContext {
        switch app {
        case "Xcode", "Visual Studio Code", "VSCode", "Code", "IntelliJ IDEA", "PyCharm", "WebStorm":
            return .code
        case "Pages", "Word", "Typora", "Bear", "Notion":
            return .document
        case let name where name.contains("飞书") || name.contains("Feishu") || 
                           name.contains("钉钉") || name.contains("DingTalk") ||
                           name.contains("微信") || name.contains("WeChat") ||
                           name.contains("Slack") || name.contains("Discord"):
            return .chat
        case "Terminal", "iTerm2", "Warp":
            return .terminal
        case "Safari", "Google Chrome", "Chrome", "Firefox", "Edge":
            return .browser
        default:
            return .unknown
        }
    }
}

/// 内容类别
enum ContentCategory: String {
    case code = "代码"
    case document = "文档"
    case chat = "聊天"
    case terminal = "命令"
    case browser = "浏览"
    case generic = "通用"
    case sensitive = "敏感"
}

/// 脱敏后的文本
struct DesensitizedText {
    let original: String           // 原始文本（不存储到数据库）
    let desensitized: String       // 脱敏后的文本
    let keywords: [String]         // 提取的关键词
    let category: ContentCategory  // 内容类别
}
