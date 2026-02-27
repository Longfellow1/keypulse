import Foundation
import SQLite3

class DatabaseManager {
    static let shared = DatabaseManager()
    private var db: OpaquePointer?
    
    private let dbPath: String = {
        let paths = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)
        let appSupport = paths[0].appendingPathComponent("KeyPulse")
        try? FileManager.default.createDirectory(at: appSupport, withIntermediateDirectories: true)
        return appSupport.appendingPathComponent("data.db").path
    }()
    
    private init() {
        openDatabase()
        createTables()
    }
    
    func openDatabase() {
        if sqlite3_open(dbPath, &db) != SQLITE_OK {
            print("❌ 无法打开数据库")
            return
        }
        print("✅ 数据库已打开：\(dbPath)")
    }
    
    func createTables() {
        let activitiesTable = """
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            app_name TEXT NOT NULL,
            window_title TEXT,
            keystroke_count INTEGER DEFAULT 0,
            duration INTEGER NOT NULL,
            desensitized_text TEXT,
            keywords TEXT,
            content_category TEXT
        )
        """
        
        if sqlite3_exec(db, activitiesTable, nil, nil, nil) != SQLITE_OK {
            print("❌ 创建 activities 表失败")
        } else {
            print("✅ 数据库表已创建")
        }
    }
    
    func insertActivity(
        timestamp: Date,
        app: String,
        window: String?,
        keystrokeCount: Int,
        duration: TimeInterval,
        desensitizedText: String?,
        keywords: [String]?,
        category: String?
    ) {
        let insertSQL = """
        INSERT INTO activities (
            timestamp, app_name, window_title, keystroke_count, duration,
            desensitized_text, keywords, content_category
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        var statement: OpaquePointer?
        if sqlite3_prepare_v2(db, insertSQL, -1, &statement, nil) == SQLITE_OK {
            sqlite3_bind_int64(statement, 1, Int64(timestamp.timeIntervalSince1970))
            sqlite3_bind_text(statement, 2, (app as NSString).utf8String, -1, nil)
            
            if let window = window {
                sqlite3_bind_text(statement, 3, (window as NSString).utf8String, -1, nil)
            } else {
                sqlite3_bind_null(statement, 3)
            }
            
            sqlite3_bind_int(statement, 4, Int32(keystrokeCount))
            sqlite3_bind_int64(statement, 5, Int64(duration))
            
            if let text = desensitizedText {
                sqlite3_bind_text(statement, 6, (text as NSString).utf8String, -1, nil)
            } else {
                sqlite3_bind_null(statement, 6)
            }
            
            if let keywords = keywords, !keywords.isEmpty {
                let keywordsStr = keywords.joined(separator: ",")
                sqlite3_bind_text(statement, 7, (keywordsStr as NSString).utf8String, -1, nil)
            } else {
                sqlite3_bind_null(statement, 7)
            }
            
            if let category = category {
                sqlite3_bind_text(statement, 8, (category as NSString).utf8String, -1, nil)
            } else {
                sqlite3_bind_null(statement, 8)
            }
            
            if sqlite3_step(statement) == SQLITE_DONE {
                // Success
            } else {
                print("❌ 写入活动失败")
            }
        }
        sqlite3_finalize(statement)
    }
    
    func getTodayActivities() -> [Activity] {
        var activities: [Activity] = []
        
        // 获取今天 00:00:00 的时间戳
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: Date())
        let todayTimestamp = Int64(today.timeIntervalSince1970)
        
        let querySQL = """
        SELECT timestamp, app_name, window_title, keystroke_count, duration,
               desensitized_text, keywords, content_category
        FROM activities
        WHERE timestamp >= ?
        ORDER BY timestamp ASC
        """
        
        var statement: OpaquePointer?
        if sqlite3_prepare_v2(db, querySQL, -1, &statement, nil) == SQLITE_OK {
            sqlite3_bind_int64(statement, 1, todayTimestamp)
            
            while sqlite3_step(statement) == SQLITE_ROW {
                let timestamp = Date(timeIntervalSince1970: TimeInterval(sqlite3_column_int64(statement, 0)))
                let app = String(cString: sqlite3_column_text(statement, 1))
                
                var window: String? = nil
                if let windowText = sqlite3_column_text(statement, 2) {
                    window = String(cString: windowText)
                }
                
                let keystrokeCount = Int(sqlite3_column_int(statement, 3))
                let duration = TimeInterval(sqlite3_column_int64(statement, 4))
                
                var desensitizedText: String? = nil
                if let text = sqlite3_column_text(statement, 5) {
                    desensitizedText = String(cString: text)
                }
                
                var keywords: [String] = []
                if let keywordsText = sqlite3_column_text(statement, 6) {
                    let keywordsStr = String(cString: keywordsText)
                    keywords = keywordsStr.components(separatedBy: ",")
                }
                
                var category: String? = nil
                if let categoryText = sqlite3_column_text(statement, 7) {
                    category = String(cString: categoryText)
                }
                
                let activity = Activity(
                    timestamp: timestamp,
                    app: app,
                    windowTitle: window,
                    keystrokeCount: keystrokeCount,
                    duration: duration,
                    desensitizedText: desensitizedText,
                    keywords: keywords,
                    contentCategory: category
                )
                activities.append(activity)
            }
        }
        sqlite3_finalize(statement)
        
        return activities
    }
    
    func clearAllData() {
        let deleteSQL = "DELETE FROM activities"
        if sqlite3_exec(db, deleteSQL, nil, nil, nil) == SQLITE_OK {
            print("✅ 所有数据已清空")
        } else {
            print("❌ 清空数据失败")
        }
    }
    
    func closeDatabase() {
        if sqlite3_close(db) != SQLITE_OK {
            print("❌ 无法关闭数据库")
        }
    }
}
