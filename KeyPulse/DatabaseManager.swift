import Foundation
import SQLite3

class DatabaseManager {
    static let shared = DatabaseManager()
    private var db: OpaquePointer?
    
    private init() {
        openDatabase()
        createTables()
    }
    
    func openDatabase() {
        sqlite3_open("~/.keypulse/data.db", &db)
        createTables()
    }
    
    func createTables() {
        let sml = "
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_start TEXT NOT NULL,
            timestamp_end TEXT,
            app_name TEXT NOT NULL,
            window_title_display TEXT
        )"
        sqlite3_exec(db, sml, nil, nil, nil)
    }
    
    func insertSession(app: String, window: String?) {
        let sml = "INSERT INTO sessions (timestamp_start, app_name, window_title_display) VALUES (?, ?, ?)"
        var stmt: OpaquePointer?
        sqlite3_prepare_v2(db, sml, -1, &stmt, nil)
        sqlite3_bind_text(stmt, 1, ISO8601DateFormatter().string(from: Date()), -1, nil)
        sqlite3_bind_text(stmt, 2, app, -1, nil)
        sqlite3_bind_text(stmt, 3, window, -1, nil)
        sqlite3_step(stmt)
        sqlite3_finalize(stmt)
    }
}
