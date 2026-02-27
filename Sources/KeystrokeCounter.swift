import Cocoa
import Foundation

/// 键盘输入捕获器 - 捕获并智能脱敏
class KeystrokeCounter {
    static let shared = KeystrokeCounter()
    private init() {}
    
    private var eventTap: CFMachPort?
    private var runLoopSource: CFRunLoopSource?
    
    // 当前输入缓冲区（按应用分组）
    private var inputBuffers: [String: InputBuffer] = [:]
    
    // 黑名单：敏感应用不捕获
    private let blacklist = [
        "1Password",
        "Keychain Access",
        "LastPass",
        "Bitwarden",
        "KeePassXC"
    ]
    
    var isRunning = false
    
    /// 启动键盘监听
    func start() {
        guard !isRunning else { return }
        
        let eventMask = (1 << CGEventType.keyDown.rawValue)
        
        guard let eventTap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .defaultTap,
            eventsOfInterest: CGEventMask(eventMask),
            callback: { (proxy, type, event, refcon) -> Unmanaged<CGEvent>? in
                KeystrokeCounter.shared.handleKeystroke(event: event)
                return Unmanaged.passRetained(event)
            },
            userInfo: nil
        ) else {
            print("❌ 无法创建事件监听器，请检查辅助功能权限")
            return
        }
        
        self.eventTap = eventTap
        self.runLoopSource = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, eventTap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), runLoopSource, .commonModes)
        CGEvent.tapEnable(tap: eventTap, enable: true)
        
        isRunning = true
        print("✅ 键盘输入捕获已启动")
    }
    
    /// 停止键盘监听
    func stop() {
        guard isRunning else { return }
        
        if let eventTap = eventTap {
            CGEvent.tapEnable(tap: eventTap, enable: false)
            CFRunLoopRemoveSource(CFRunLoopGetCurrent(), runLoopSource, .commonModes)
        }
        
        isRunning = false
        print("⏹️ 键盘输入捕获已停止")
    }
    
    /// 处理键击事件
    private func handleKeystroke(event: CGEvent) {
        guard let app = NSWorkspace.shared.frontmostApplication else { return }
        let appName = app.localizedName ?? "Unknown"
        
        // 黑名单过滤
        guard !blacklist.contains(appName) else { return }
        
        // 提取按键字符
        let maxLength = 10
        var length = 0
        var buffer = [UniChar](repeating: 0, count: maxLength)
        
        event.keyboardGetUnicodeString(
            maxStringLength: maxLength,
            actualStringLength: &length,
            unicodeString: &buffer
        )
        
        guard length > 0 else { return }
        
        let characters = String(utf16CodeUnits: buffer, count: length)
        
        // 添加到缓冲区
        if inputBuffers[appName] == nil {
            let context = InputContext.from(app: appName)
            inputBuffers[appName] = InputBuffer(app: appName, context: context)
        }
        
        inputBuffers[appName]?.append(characters)
    }
    
    /// 获取并重置指定应用的输入数据
    func getAndResetInput(for app: String) -> CapturedInput? {
        guard let buffer = inputBuffers[app], !buffer.isEmpty else {
            return nil
        }
        
        let input = buffer.finalize()
        inputBuffers[app] = nil
        
        return input
    }
    
    /// 清空所有缓冲区
    func reset() {
        inputBuffers.removeAll()
    }
}

// MARK: - 输入缓冲区

/// 输入缓冲区
class InputBuffer {
    let app: String
    let context: InputContext
    private var buffer: String = ""
    private var keystrokeCount: Int = 0
    private let startTime: Date = Date()
    
    // 缓冲区配置
    private let maxBufferSize = 1000  // 最多缓存 1000 个字符
    private let flushInterval: TimeInterval = 60.0  // 60 秒自动刷新
    
    init(app: String, context: InputContext) {
        self.app = app
        self.context = context
    }
    
    var isEmpty: Bool {
        return buffer.isEmpty
    }
    
    /// 添加字符
    func append(_ characters: String) {
        buffer += characters
        keystrokeCount += characters.count
        
        // 防止缓冲区过大
        if buffer.count > maxBufferSize {
            buffer = String(buffer.suffix(maxBufferSize))
        }
    }
    
    /// 完成并生成捕获数据
    func finalize() -> CapturedInput {
        let duration = Date().timeIntervalSince(startTime)
        
        // 脱敏处理
        let desensitized = TextDesensitizer.shared.desensitize(buffer, context: context)
        
        return CapturedInput(
            app: app,
            context: context,
            originalText: buffer,
            desensitizedText: desensitized.desensitized,
            keywords: desensitized.keywords,
            category: desensitized.category,
            keystrokeCount: keystrokeCount,
            duration: duration
        )
    }
}

// MARK: - 捕获的输入数据

/// 捕获的输入数据
struct CapturedInput {
    let app: String
    let context: InputContext
    let originalText: String           // 原始文本（不存储）
    let desensitizedText: String       // 脱敏后的文本
    let keywords: [String]             // 关键词
    let category: ContentCategory      // 内容类别
    let keystrokeCount: Int            // 键击次数
    let duration: TimeInterval         // 持续时间
    
    /// 生成摘要
    var summary: String {
        if keywords.isEmpty {
            return desensitizedText
        } else {
            return keywords.joined(separator: ", ")
        }
    }
}
