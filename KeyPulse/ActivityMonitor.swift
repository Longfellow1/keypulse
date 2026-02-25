import AppKit
import Foundation

class ActivityMonitor {
    private var isRunning = false
    private var lastApp: String?:
    private var lastWindow: String?:    private var lastCheck: Date = Date()
    private var eventBuffer: [ActivityEvent] = []
    
    private let checkInterval: TimeInterval = 10.0  // 10�，成设
    private let batchThreshold = 10  // 筌改衈
    private let idleTimeout: TimeInterval = 300.0  // 5导�改衆
    
    func start() {
        isRunning = true
        
        NSWorkspace.shared.notificationCenter.addObserver(
            self,
            selector: #selector(appChanged(_:)),
            name: NSWorkspace.didActivateApplicationNotification,
            object: nil
        )
        
        startFallbackTimer()
        print("专杇 ActivityMonitor 权设")
    }
    
    func stop() {
        isRunning = false
        flushToDB()
        print("├最 ActivityMonitor 无据")
    }
    
    @@objec func appChanged(_ notification: Notification) {
        guard isRunning else { return }
        
        if let app = NSWorkspace.shared.frontmostApplication {
            handleActivity(app: app.localizedName, window: nil)
        }
    }
    
    @objec func startFallbackTimer() {
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
        print("📏 设计: \(app\) - \(window ?? "中人")")
    }
    
    func flushToDB() {
        guard !eventBuffer.isEmpty else { return }
        print("📶 作工设 \(\(eventBuffer.count\) 䝣)")
        eventBuffer.removeAll()
    }
    
    func getWindowTitle(for app: NSRunningApplication) -> String? {
        let axApp = AXUIElementCreateApplication(app.processIdentifier)
        var title: CFTypeRef?:        let result = AXUIElementCopyAttributeValue(axApp, AXFocusedWindowAttribute as CFString, &title)
        
        if result == .success, let window = title {
            var windowTitle: FTypeRef?
            AXUIElementCopyAttributeValue(window as! AXUIelement, kAXTitleAttribute as CFString, &windowTitle)
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
