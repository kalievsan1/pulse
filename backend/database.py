"""SQLite database module — connection management, schema initialization, indexes."""

import sqlite3
import os
import logging
from config import Config

logger = logging.getLogger(__name__)


def _resolve_db_path(configured_path: str) -> str:
    """Return a writable DB path, fallback to local backend file if needed."""
    fallback_path = os.path.join(os.path.dirname(__file__), 'entertainment.db')
    candidate = configured_path or fallback_path

    candidate_dir = os.path.dirname(candidate) or '.'
    try:
        os.makedirs(candidate_dir, exist_ok=True)
        return candidate
    except Exception:
        fallback_dir = os.path.dirname(fallback_path) or '.'
        os.makedirs(fallback_dir, exist_ok=True)
        if candidate != fallback_path:
            logger.warning(
                'DATABASE_PATH=%s is not writable/creatable, falling back to %s',
                candidate,
                fallback_path,
            )
        return fallback_path


DB_PATH = _resolve_db_path(Config.DATABASE_PATH)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            is_blocked INTEGER DEFAULT 0,
            daily_limit INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_type TEXT NOT NULL,
            item_id TEXT NOT NULL,
            title TEXT NOT NULL,
            image_url TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, item_type, item_id)
        );

        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_type TEXT NOT NULL,
            item_id TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, item_type, item_id)
        );

        CREATE TABLE IF NOT EXISTS user_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            genre TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            category TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, genre, category)
        );

        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            query TEXT NOT NULL,
            category TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS ai_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            favorite_categories TEXT DEFAULT '',
            disliked_categories TEXT DEFAULT '',
            favorite_platforms TEXT DEFAULT '',
            preferred_language TEXT DEFAULT 'ru',
            age_rating TEXT DEFAULT 'any',
            discovery_mode TEXT DEFAULT 'balanced',
            onboarding_completed INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS ai_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            user_query TEXT,
            ai_response TEXT,
            ai_response_json TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS ai_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id TEXT,
            query_text TEXT,
            title TEXT NOT NULL,
            category TEXT,
            feedback_type TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS admin_content_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            category TEXT,
            rule_type TEXT NOT NULL,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS admin_pinned (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            year_genre TEXT,
            description TEXT NOT NULL,
            category TEXT,
            why_this TEXT,
            video_id TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS admin_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_type TEXT NOT NULL,
            item_id TEXT NOT NULL,
            title TEXT NOT NULL,
            image_url TEXT,
            metadata TEXT,
            note TEXT DEFAULT '',
            priority INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, item_type, item_id)
        );

        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            endpoint TEXT NOT NULL,
            model_name TEXT,
            status_code INTEGER NOT NULL,
            source TEXT,
            query_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_type TEXT NOT NULL,
            item_id TEXT NOT NULL,
            title TEXT NOT NULL,
            image_url TEXT,
            action TEXT NOT NULL DEFAULT 'watched',
            rating INTEGER,
            note TEXT DEFAULT '',
            consumed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, item_type, item_id)
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL DEFAULT 'info',
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            link TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_type TEXT NOT NULL,
            item_id TEXT NOT NULL,
            title TEXT NOT NULL,
            remind_at TIMESTAMP NOT NULL,
            note TEXT DEFAULT '',
            is_done INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT DEFAULT '',
            target_id INTEGER,
            details TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (admin_id) REFERENCES users(id)
        );

        -- Performance indexes
        CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id);
        CREATE INDEX IF NOT EXISTS idx_ratings_user ON ratings(user_id);
        CREATE INDEX IF NOT EXISTS idx_ai_history_user_ts ON ai_history(user_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_ai_history_session ON ai_history(session_id);
        CREATE INDEX IF NOT EXISTS idx_ai_feedback_user ON ai_feedback(user_id);
        CREATE INDEX IF NOT EXISTS idx_activity_user_ts ON activity_log(user_id, consumed_at);
        CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read);
        CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id);
        CREATE INDEX IF NOT EXISTS idx_search_history_user ON search_history(user_id);
        CREATE INDEX IF NOT EXISTS idx_api_usage_user ON api_usage(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(user_id, is_done);
        CREATE INDEX IF NOT EXISTS idx_audit_log_admin ON admin_audit_log(admin_id, created_at);
    ''')

    conn.commit()

    # Migrate existing users table — add new columns if missing
    try:
        cols = [row[1] for row in cursor.execute("PRAGMA table_info('users')").fetchall()]
        if 'is_admin' not in cols:
            cursor.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
        if 'is_blocked' not in cols:
            cursor.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0")
        if 'daily_limit' not in cols:
            cursor.execute("ALTER TABLE users ADD COLUMN daily_limit INTEGER")
        conn.commit()
    except Exception:
        logger.exception('Migration error for users table')

    # Make first user admin if no admins exist
    try:
        admin = cursor.execute("SELECT id FROM users WHERE is_admin = 1").fetchone()
        if not admin:
            first = cursor.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
            if first:
                cursor.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (first[0],))
                conn.commit()
    except Exception:
        logger.exception('Error setting initial admin')

    # Init default admin settings
    try:
        cursor.execute("INSERT OR IGNORE INTO admin_settings (key, value) VALUES ('force_lite_mode', '0')")
        cursor.execute("INSERT OR IGNORE INTO admin_settings (key, value) VALUES ('default_daily_limit', '40')")
        conn.commit()
    except Exception:
        logger.exception('Error initializing admin settings')

    conn.close()


def apply_admin_password_reset_from_env():
    """Reset or create an admin user when ADMIN_RESET_PASSWORD is configured."""
    password = os.environ.get('ADMIN_RESET_PASSWORD', '').strip()
    if not password:
        return

    username = os.environ.get('ADMIN_RESET_USERNAME', 'King').strip() or 'King'
    email = os.environ.get('ADMIN_RESET_EMAIL', 'asanalivoin@gmail.com').strip() or 'asanalivoin@gmail.com'

    from werkzeug.security import generate_password_hash

    conn = get_db()
    cursor = conn.cursor()

    try:
        user = cursor.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        password_hash = generate_password_hash(password)

        if user:
            cursor.execute(
                """
                UPDATE users
                SET password_hash = ?, is_admin = 1, is_blocked = 0
                WHERE username = ?
                """,
                (password_hash, username),
            )
        else:
            cursor.execute(
                """
                INSERT INTO users (username, email, password_hash, is_admin, is_blocked)
                VALUES (?, ?, ?, 1, 0)
                """,
                (username, email, password_hash),
            )

        conn.commit()
        logger.info('Admin password reset applied for user %s', username)
    except Exception:
        logger.exception('Admin password reset failed for user %s', username)
        raise
    finally:
        conn.close()
