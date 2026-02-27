import Foundation

/// 工作强度等级
enum WorkIntensity: String {
    case high = "高强度"
    case medium = "中强度"
    case low = "低强度"
    case idle = "空闲"
    
    /// 根据键击频率判断工作强度
    static func from(keystrokeCount: Int, durationMinutes: Double) -> WorkIntensity {
        guard durationMinutes > 0 else { return .idle }
        
        let keystrokesPerMinute = Double(keystrokeCount) / durationMinutes
        
        if keystrokesPerMinute > 20 {
            return .high
        } else if keystrokesPerMinute > 5 {
            return .medium
        } else if keystrokesPerMinute > 0 {
            return .low
        } else {
            return .idle
        }
    }
}

/// 活动记录
struct Activity {
    let timestamp: Date
    let app: String
    let windowTitle: String?
    let keystrokeCount: Int
    let duration: TimeInterval
    let desensitizedText: String?
    let keywords: [String]
    let contentCategory: String?
    
    var project: String {
        return ProjectExtractor.shared.extractProject(from: app, windowTitle: windowTitle)
    }
    
    var intensity: WorkIntensity {
        return WorkIntensity.from(
            keystrokeCount: keystrokeCount,
            durationMinutes: duration / 60.0
        )
    }
}

/// 项目工作记录
struct ProjectWork {
    let projectName: String
    var activities: [Activity] = []
    
    var totalDuration: TimeInterval {
        return activities.reduce(0) { $0 + $1.duration }
    }
    
    var totalKeystrokes: Int {
        return activities.reduce(0) { $0 + $1.keystrokeCount }
    }
    
    var durationHours: Double {
        return totalDuration / 3600.0
    }
    
    var allKeywords: [String] {
        var keywords: [String] = []
        for activity in activities {
            keywords.append(contentsOf: activity.keywords)
        }
        return Array(Set(keywords))
    }
}

/// 日报生成器
class ReportGenerator {
    static let shared = ReportGenerator()
    private init() {}
    
    /// 生成今日工作日报
    func generateDailyReport(activities: [Activity]) -> String {
        guard !activities.isEmpty else {
            return "📭 今日暂无工作记录"
        }
        
        // 按项目分组
        let projectWorks = groupByProject(activities)
        
        // 生成 Markdown 报告
        var report = ""
        
        // 标题
        let dateFormatter = DateFormatter()
        dateFormatter.dateFormat = "yyyy-MM-dd"
        let today = dateFormatter.string(from: Date())
        report += "## \(today) 工作日报\n\n"
        
        // 按项目输出
        for projectWork in projectWorks.sorted(by: { $0.totalDuration > $1.totalDuration }) {
            report += generateProjectSection(projectWork)
        }
        
        // 统计摘要
        report += generateSummary(projectWorks)
        
        return report
    }
    
    /// 按项目分组活动
    private func groupByProject(_ activities: [Activity]) -> [ProjectWork] {
        var projectMap: [String: ProjectWork] = [:]
        
        for activity in activities {
            let projectName = activity.project
            if projectMap[projectName] == nil {
                projectMap[projectName] = ProjectWork(projectName: projectName)
            }
            projectMap[projectName]?.activities.append(activity)
        }
        
        return Array(projectMap.values)
    }
    
    /// 生成项目工作段落
    private func generateProjectSection(_ projectWork: ProjectWork) -> String {
        var section = ""
        
        // 项目标题
        let hours = String(format: "%.1f", projectWork.durationHours)
        section += "### \(projectWork.projectName)（\(hours)h）\n"
        
        // 合并相邻的相似活动
        let mergedActivities = mergeActivities(projectWork.activities)
        
        // 输出活动列表
        for activity in mergedActivities {
            section += formatActivity(activity)
        }
        
        // 输出项目关键词
        let keywords = projectWork.allKeywords
        if !keywords.isEmpty {
            section += "  **关键词：** \(keywords.joined(separator: ", "))\n"
        }
        
        section += "\n"
        return section
    }
    
    /// 合并相邻的相似活动
    private func mergeActivities(_ activities: [Activity]) -> [Activity] {
        guard !activities.isEmpty else { return [] }
        
        var merged: [Activity] = []
        var current = activities[0]
        
        for i in 1..<activities.count {
            let next = activities[i]
            
            // 如果应用相同且时间间隔小于 5 分钟，则合并
            if current.app == next.app && 
               next.timestamp.timeIntervalSince(current.timestamp) < 300 {
                
                // 合并关键词
                var mergedKeywords = current.keywords
                mergedKeywords.append(contentsOf: next.keywords)
                
                current = Activity(
                    timestamp: current.timestamp,
                    app: current.app,
                    windowTitle: current.windowTitle,
                    keystrokeCount: current.keystrokeCount + next.keystrokeCount,
                    duration: current.duration + next.duration,
                    desensitizedText: current.desensitizedText,
                    keywords: Array(Set(mergedKeywords)),
                    contentCategory: current.contentCategory
                )
            } else {
                merged.append(current)
                current = next
            }
        }
        merged.append(current)
        
        return merged
    }
    
    /// 格式化单个活动
    private func formatActivity(_ activity: Activity) -> String {
        let timeFormatter = DateFormatter()
        timeFormatter.dateFormat = "HH:mm"
        
        let startTime = timeFormatter.string(from: activity.timestamp)
        let endTime = timeFormatter.string(from: activity.timestamp.addingTimeInterval(activity.duration))
        
        let appInfo = activity.windowTitle ?? activity.app
        let intensity = activity.intensity.rawValue
        
        var details = "（\(activity.app)，\(intensity)"
        
        if activity.keystrokeCount > 0 {
            details += "，\(activity.keystrokeCount) 键击"
        }
        
        if let category = activity.contentCategory {
            details += "，\(category)"
        }
        
        details += "）"
        
        var line = "- \(startTime)-\(endTime) \(appInfo)\(details)\n"
        
        // 如果有脱敏文本，添加内容摘要
        if let text = activity.desensitizedText, !text.isEmpty, text != "[敏感内容已过滤]" {
            line += "  内容：\(text)\n"
        }
        
        return line
    }
    
    /// 生成统计摘要
    private func generateSummary(_ projectWorks: [ProjectWork]) -> String {
        let totalDuration = projectWorks.reduce(0) { $0 + $1.totalDuration }
        let totalKeystrokes = projectWorks.reduce(0) { $0 + $1.totalKeystrokes }
        
        let hours = Int(totalDuration / 3600)
        let minutes = Int((totalDuration.truncatingRemainder(dividingBy: 3600)) / 60)
        
        // 统计各类活动时长
        var categoryDurations: [String: TimeInterval] = [:]
        for projectWork in projectWorks {
            for activity in projectWork.activities {
                if let category = activity.contentCategory {
                    categoryDurations[category, default: 0] += activity.duration
                }
            }
        }
        
        var summary = "---\n"
        summary += "💡 **今日工作统计**\n\n"
        summary += "- 总时长：\(hours)h\(minutes)m\n"
        
        if totalKeystrokes > 0 {
            summary += "- 总键击：\(totalKeystrokes) 次\n"
        }
        
        // 按类别统计
        if !categoryDurations.isEmpty {
            summary += "- 活动分布：\n"
            for (category, duration) in categoryDurations.sorted(by: { $0.value > $1.value }) {
                let h = Int(duration / 3600)
                let m = Int((duration.truncatingRemainder(dividingBy: 3600)) / 60)
                summary += "  - \(category)：\(h)h\(m)m\n"
            }
        }
        
        summary += "\n"
        
        return summary
    }
}
