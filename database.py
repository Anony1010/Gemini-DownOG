import sqlite3
import os
import threading
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_database.db")

# Thread-safe settings cache
_settings_cache = {}
_cache_lock = threading.Lock()

@contextmanager
def get_db():
    """Context manager for SQLite connections with WAL mode and timeout settings."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        # Enable WAL (Write-Ahead Logging) for concurrent reads/writes and speed
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    """Initialize database tables and indexes."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            is_banned INTEGER DEFAULT 0,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            url TEXT,
            platform TEXT,
            downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS clones (
            token TEXT PRIMARY KEY,
            username TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        
        # Optimize with indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_joined_at ON users(joined_at);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_downloads_user_id ON downloads(user_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_downloads_downloaded_at ON downloads(downloaded_at);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_clones_token ON clones(token);")
        
        # Warm up the settings cache
        cursor.execute("SELECT key, value FROM settings")
        with _cache_lock:
            for row in cursor.fetchall():
                _settings_cache[row['key']] = row['value']

def db_register_user(user_id, username, first_name):
    """Register user or update details with optimized ON CONFLICT."""
    with get_db() as conn:
        conn.execute("""
        INSERT INTO users (user_id, username, first_name)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name
        """, (user_id, username, first_name))

def db_is_banned(user_id):
    """Check if user is banned using indexed query."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return row['is_banned'] == 1
        return False

def db_ban_user(user_id, is_banned):
    """Ban or unban user."""
    with get_db() as conn:
        conn.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (is_banned, user_id))

def db_log_download(user_id, url, platform):
    """Log a download event."""
    with get_db() as conn:
        conn.execute("INSERT INTO downloads (user_id, url, platform) VALUES (?, ?, ?)", (user_id, url, platform))

def db_get_user(user_id):
    """Get user details by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def db_get_user_by_username(username):
    """Get user details by username."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        return dict(row) if row else None

def db_get_all_users():
    """Get list of all users."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users")
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

def db_get_recent_users(limit=15):
    """Get recent users ordered by joined_at descending."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users ORDER BY joined_at DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

def db_count_users():
    """Count total registered users."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM users")
        row = cursor.fetchone()
        return row['cnt'] if row else 0

def db_count_banned_users():
    """Count total banned users."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM users WHERE is_banned = 1")
        row = cursor.fetchone()
        return row['cnt'] if row else 0

def db_get_user_downloads(user_id, limit=5):
    """Get recent downloads for a user."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM downloads WHERE user_id = ? ORDER BY downloaded_at DESC LIMIT ?", (user_id, limit))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

def get_setting(key):
    """Get a setting value (utilizing fast in-memory cache)."""
    with _cache_lock:
        if key in _settings_cache:
            return _settings_cache[key]
            
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        val = row['value'] if row else None
        
        with _cache_lock:
            _settings_cache[key] = val
        return val

def set_setting(key, value):
    """Set a setting value and update cache."""
    val_str = str(value) if value is not None else None
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, val_str))
        
    with _cache_lock:
        _settings_cache[key] = val_str

def delete_setting(key):
    """Delete a setting and remove from cache."""
    with get_db() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        
    with _cache_lock:
        _settings_cache.pop(key, None)

def db_add_clone(token, username):
    """Register a cloned bot token."""
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO clones (token, username) VALUES (?, ?)", (token, username))

def db_get_all_clones():
    """Get list of all cloned bots."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM clones")
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

def db_delete_clone(token):
    """Delete a cloned bot token."""
    with get_db() as conn:
        conn.execute("DELETE FROM clones WHERE token = ?", (token,))
