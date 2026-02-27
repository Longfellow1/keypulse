import AppKit
import Foundation

class ActivityMonitor {
    static let shared = ActivityMonitor()
    private init() {}
    
    var isRunning = false
    var lastApp: String?
    var lastWindow: String?
    var lastCheck: Date = Date()
    var sessionStart: Date = Date()
    
    // 低功耗配置
    let checkInterval: TimeInterval = 10.0  // 10 秒校验
    let idleTimeout: TimeInterval = 300.0  // 5 分钟无活动暂停
    
    func start() {
        isRunning = true
        sessionStart = Date()
        
        // 启动键盘输入捕获
        KeystrokeCounter.shared.start()
        
        // 1. 事件驱动监听
        NSWorkspace.shared.notificationCenter.addObserver(
            self,
            selector: #selector(appChanged(_:)),
            name: NSWorkspace.didActivateApplicationNotification,
            object: nil
        )
        
        // 2. 低频轮询兜底
        startFallbackTimer()
        
        print("✅ ActivityMonitor 已启动")
    }
    
    func stop() {
        isRunning = false
        
        // 保存最后一次活动
        saveCurrentActivity()
        
        // 停止键盘输入捕获
        KeystrokeCounter.shared.stop()
        
        print("⏹️ ActivityMonitor 已停止")
    }
    
    @objc func appChanged(_ notification: Notification) {
        guard isRunning else { return }
        
        // 保存上一个活动
        saveCurrentActivity()
        
        // 开始新活动
        if let app = NSWorkspace.shared.frontmostApplication {
            lastApp = app.localizedName
            lastWindow = getWindowTitle(for: app)
            lastCheck = Date()
        }
    }
    
    func startFallbackTimer() {
        Timer.scheduledTimer(withTimeInterval: checkInterval, repeats: true) { [weak self] _ in
            self?.validateIfNeeded()
        }
    }
    
    func validateIfNeeded() {
        guard isRunning else { return }
        
        guard let app = NSWorkspace.shared.frontmostApplication else { return }
        let appName = app.localizedName
        let windowTitle = getWindowTitle(for: app)
        
        // 如果应用或窗口变化，保存旧活动并开始新活动
        if appName != lastApp || windowTitle != lastWindow {
            saveCurrentActivity()
            lastApp = appName
            lastWindow = windowTitle
            lastCheck = Date()
        }
    }
    
    func saveCurrentActivity() {
        guard let app = lastApp else { return }
        
        let now = Date()
        let duration = now.timeIntervalSince(lastCheck)
        
        // 过滤太短的活动（< 3 秒）
        guard duration >= 3.0 else { return }
        
        // 获取捕获的输入数据
        let capturedInput = KeystrokeCounter.shared.getAndResetInput(for: app)
        
        // 提取数据
        let keystrokeCount = capturedInput?.keystrokeCount ?? 0
        let desensitizedText = capturedInput?.desensitizedText
        let keywords = capturedInput?.keywords ?? []
        let category = capturedInput?.category.rawValue
        
        // 保存到数据库
        DatabaseManager.shared.insertActivity(
            timestamp: lastCheck,
            app: app,
            window: lastWindow,
            keystrokeCount: keystrokeCount,
            duration: duration,
            desensitizedText: desensitizedText,
            keywords: keywords,
            category: category
        )
        
        // 打印日志
        var logMessage = "💾 保存活动：\(app)"
        if let window = lastWindow {
            logMessage += " - \(window)"
        }
        logMessage += " (\(Int(duration))秒, \(keystrokeCount)键击"
        if let text = desensitizedText, !text.isEmpty {
            logMessage += ", 内容: \(text.prefix(50))..."
        }
        logMessage += ")"
        
        print(logMessage)
    }
    
    func getWindowTitle(for app: NSRunningApplication?) -> String? {
        guard let app = app else { return nil }
        
        let axApp = AXUIElementCreateApplication(app.processIdentifier)
        var focusedWindow: CFTypeRef?
        
        let result = AXUIElementCopyAttributeValue(
            axApp,
            kAXFocusedWindowAttribute as CFString,
            &focusedWindow
        )
        
        if result == .success, let window = focusedWindow {
            var title: CFTypeRef?
            AXUIElementCopyAttributeValue(
                window as! AXUIElement,
                kAXTitleAttribute as CFString,
                &title
            )
            return title as? String
        }
        
        return nil
    }
}

struct ActivityEvent {
    let timestamp: Date
    let app: String
    let window: String?
    let duration: TimeInterval
}
