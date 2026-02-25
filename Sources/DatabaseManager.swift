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
        let sessionsTable = """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_start TEXT NOT NULL,
            timestamp_end TEXT,
            app_name TEXT NOT NULL,
            app_bundle_id TEXT,
            window_title_hash TEXT,
            window_title_display TEXT,
            url_domain TEXT,
            is_manual INTEGER DEFAULT 0,
            tags TEXT
        )
        """
        
        let filterLogTable = """
        CREATE TABLE IF NOT EXISTS filter_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            reason TEXT NOT NULL,
            app_name TEXT,
            url TEXT,
            window_title TEXT
        )
        """
        
        let markersTable = """
        CREATE TABLE IF NOT EXISTS manual_markers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            note TEXT,
            session_id INTEGER
        )
        """
        
        if sqlite3_exec(db, sessionsTable, nil, nil, nil) != SQLITE_OK {
            print("❌ 创建 sessions 表失败")
        }
        
        if sqlite3_exec(db, filterLogTable, nil, nil, nil) != SQLITE_OK {
            print("❌ 创建 filter_log 表失败")
        }
        
        if sqlite3_exec(db, markersTable, nil, nil, nil) != SQLITE_OK {
            print("❌ 创建 manual_markers 表失败")
        }
        
        print("✅ 数据库表已创建")
    }
    
    func insertSession(event: ActivityEvent) {
        let insertSQL = """
        INSERT INTO sessions (timestamp_start, app_name, window_title_display)
        VALUES (?, ?, ?)
        """
        
        var statement: OpaquePointer?
        if sqlite3_prepare_v2(db, insertSQL, -1, &statement, nil) == SQLITE_OK {
            let timestamp = ISO8601DateFormatter().string(from: event.timestamp)
            sqlite3_bind_text(statement, 1, timestamp, -1, nil)
            sqlite3_bind_text(statement, 2, event.app, -1, nil)
            sqlite3_bind_text(statement, 3, event.window, -1, nil)
            
            if sqlite3_step(statement) == SQLITE_DONE {
                print("✅ 会话已写入")
            } else {
                print("❌ 写入失败")
            }
        }
        sqlite3_finalize(statement)
    }
    
    func getTodaySessions() -> [String] {
        var sessions: [String] = []
        let querySQL = "SELECT app_name, window_title_display FROM sessions WHERE date(timestamp_start) = date('now')"
        
        var statement: OpaquePointer?
        if sqlite3_prepare_v2(db, querySQL, -1, &statement, nil) == SQLITE_OK {
            while sqlite3_step(statement) == SQLITE_ROW {
                let app = String(cString: sqlite3_column_text(statement, 0))
                let window = String(cString: sqlite3_column_text(statement, 1))
                sessions.append("\(app) - \(window)")
            }
        }
        sqlite3_finalize(statement)
        
        return sessions
    }
    
    func closeDatabase() {
        if sqlite3_close(db) != SQLITE_OK {
            print("❌ 无法关闭数据库")
        }
    }
}
