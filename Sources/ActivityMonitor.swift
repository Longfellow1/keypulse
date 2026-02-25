import AppKit
import Foundation

class ActivityMonitor {
    static let shared = ActivityMonitor()
    private init() {}
    
    var isRunning = false
    var lastApp: String?
    var lastWindow: String?
    var lastCheck: Date = Date()
    var eventBuffer: [ActivityEvent] = []
    
    // 低功耗配置
    let checkInterval: TimeInterval = 10.0  // 10 秒校验
    let batchThreshold = 10  // 累积 10 条写入
    let idleTimeout: TimeInterval = 300.0  // 5 分钟无活动暂停
    
    func start() {
        isRunning = true
        
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
        flushToDB()
        print("⏹️ ActivityMonitor 已停止")
    }
    
    @objc func appChanged(_ notification: Notification) {
        guard isRunning else { return }
        if let app = NSWorkspace.shared.frontmostApplication {
            handleActivity(app: app.localizedName, window: nil)
        }
    }
    
    @objc func startFallbackTimer() {
        Timer.scheduledTimer(withTimeInterval: checkInterval, repeats: true) { [weak self] _ in
            self?.validateIfNeeded()
        }
    }
    
    func validateIfNeeded() {
        guard isRunning else { return }
        let app = NSWorkspace.shared.frontmostApplication
        let appName = app.localizedName
        let windowTitle = getWindowTitle(for: app)
        
        if appName != lastApp || windowTitle != lastWindow {
            handleActivity(app: appName, window: windowTitle)
        }
    }
    
    func handleActivity(app: String?, window: String?) {
        guard let app = app else { return }
        
        // 快速闪切过滤（< 3 秒忽略）
        let now = Date()
        if now.timeIntervalSince(lastCheck) < 3.0 { return }
        lastCheck = now
        
        let event = ActivityEvent(timestamp: now, app: app, window: window, duration: now.timeIntervalSince(lastCheck))
        eventBuffer.append(event)
        
        if eventBuffer.count >= batchThreshold {
            flushToDB()
        }
        
        lastApp = app
        lastWindow = window
        print("📝 记录：\(app) - \(window ?? "无窗口")")
    }
    
    func flushToDB() {
        guard !eventBuffer.isEmpty else { return }
        print("💾 批量写入 \(eventBuffer.count) 条事件到数据库")
        eventBuffer.removeAll()
    }
    
    func getWindowTitle(for app: NSRunningApplication) -> String? {
        let axApp = AXUIElementCreateApplication(app.processIdentifier)
        var title: CFTypeRef?
        let result = AXUIElementCopyAttributeValue(axApp, kAXFocusedWindowAttribute as CFString, &title)
        if result == .success, let window = title {
            var windowTitle: CFTypeRef?
            AXUIElementCopyAttributeValue(window as! AXUIElement, kAXTitleAttribute as CFString, &windowTitle)
            return windowTitle as? String
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
