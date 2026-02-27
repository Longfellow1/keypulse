import Foundation

/// 项目名提取器 - 从窗口标题智能提取项目名
class ProjectExtractor {
    static let shared = ProjectExtractor()
    private init() {}
    
    /// 从窗口标题提取项目名
    func extractProject(from app: String, windowTitle: String?) -> String {
        guard let title = windowTitle, !title.isEmpty else {
            return app
        }
        
        // 根据不同应用使用不同的提取规则
        switch app {
        case "Xcode":
            return extractFromXcode(title)
        case "Visual Studio Code", "VSCode", "Code":
            return extractFromVSCode(title)
        case "Terminal", "iTerm2", "Warp":
            return extractFromTerminal(title)
        case "Safari", "Google Chrome", "Chrome", "Firefox":
            return extractFromBrowser(title)
        case let name where name.contains("飞书") || name.contains("Feishu"):
            return extractFromFeishu(title)
        case let name where name.contains("钉钉") || name.contains("DingTalk"):
            return "钉钉会议"
        case "Zoom", "腾讯会议", "Microsoft Teams":
            return "会议"
        default:
            return extractGeneric(title) ?? app
        }
    }
    
    // MARK: - 应用特定提取规则
    
    private func extractFromXcode(_ title: String) -> String {
        // "MyApp - AppDelegate.swift" -> "MyApp"
        if let projectName = title.components(separatedBy: " - ").first {
            return projectName.trimmingCharacters(in: .whitespaces)
        }
        return "Xcode"
    }
    
    private func extractFromVSCode(_ title: String) -> String {
        // "keypulse/main.swift - Visual Studio Code" -> "keypulse"
        // "main.swift - keypulse" -> "keypulse"
        
        let cleaned = title
            .replacingOccurrences(of: " - Visual Studio Code", with: "")
            .replacingOccurrences(of: " - VSCode", with: "")
            .replacingOccurrences(of: " - Code", with: "")
        
        // 提取路径中的项目名
        if let projectName = cleaned.components(separatedBy: "/").first {
            return projectName.trimmingCharacters(in: .whitespaces)
        }
        
        // 提取 "file - project" 格式
        if let projectName = cleaned.components(separatedBy: " - ").last {
            return projectName.trimmingCharacters(in: .whitespaces)
        }
        
        return "VSCode"
    }
    
    private func extractFromTerminal(_ title: String) -> String {
        // "~/projects/keypulse" -> "keypulse"
        // "bash - keypulse" -> "keypulse"
        
        if title.contains("~/") || title.contains("/") {
            let components = title.components(separatedBy: "/")
            if let projectName = components.last, !projectName.isEmpty {
                return projectName.trimmingCharacters(in: .whitespaces)
            }
        }
        
        if let projectName = title.components(separatedBy: " - ").last {
            return projectName.trimmingCharacters(in: .whitespaces)
        }
        
        return "Terminal"
    }
    
    private func extractFromBrowser(_ title: String) -> String {
        // "GitHub - Longfellow1/keypulse" -> "GitHub"
        // "Stack Overflow - How to..." -> "Stack Overflow"
        
        // 提取域名关键词
        if title.contains("GitHub") || title.contains("github.com") {
            return "GitHub"
        }
        if title.contains("Stack Overflow") || title.contains("stackoverflow.com") {
            return "Stack Overflow"
        }
        if title.contains("Google") {
            return "Google 搜索"
        }
        if title.contains("YouTube") {
            return "YouTube"
        }
        
        // 提取第一个 " - " 之前的内容
        if let siteName = title.components(separatedBy: " - ").first {
            return siteName.trimmingCharacters(in: .whitespaces)
        }
        
        return "浏览器"
    }
    
    private func extractFromFeishu(_ title: String) -> String {
        // "产品需求评审 - 飞书" -> "产品需求评审"
        if let meetingName = title.components(separatedBy: " - ").first {
            return meetingName.trimmingCharacters(in: .whitespaces)
        }
        return "飞书"
    }
    
    private func extractGeneric(_ title: String) -> String? {
        // 通用规则：提取 " - " 之前的内容
        if let name = title.components(separatedBy: " - ").first {
            let cleaned = name.trimmingCharacters(in: .whitespaces)
            if !cleaned.isEmpty && cleaned.count < 50 {
                return cleaned
            }
        }
        return nil
    }
}
