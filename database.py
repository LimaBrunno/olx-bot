import sqlite3
from datetime import datetime, date
from config import DB_PATH


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sent_messages (
            ad_id       TEXT PRIMARY KEY,
            ad_title    TEXT,
            ad_url      TEXT,
            ad_price    TEXT,
            message     TEXT,
            sent_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS execution_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at  TIMESTAMP,
            finished_at TIMESTAMP,
            search_term TEXT,
            total_found INTEGER DEFAULT 0,
            total_sent  INTEGER DEFAULT 0,
            total_skipped INTEGER DEFAULT 0,
            status      TEXT DEFAULT 'running'
        );
    """)
    conn.commit()
    conn.close()


def was_already_sent(ad_id: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM sent_messages WHERE ad_id = ?", (ad_id,)
    ).fetchone()
    conn.close()
    return row is not None


def log_sent_message(ad_id: str, ad_title: str, ad_url: str, ad_price: str, message: str):
    conn = get_connection()
    conn.execute(
        """INSERT OR IGNORE INTO sent_messages (ad_id, ad_title, ad_url, ad_price, message)
           VALUES (?, ?, ?, ?, ?)""",
        (ad_id, ad_title, ad_url, ad_price, message),
    )
    conn.commit()
    conn.close()


def get_today_sent_count() -> int:
    conn = get_connection()
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM sent_messages WHERE DATE(sent_at) = ?", (today,)
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def start_execution(search_term: str) -> int:
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO execution_log (started_at, search_term, status)
           VALUES (?, ?, 'running')""",
        (datetime.now().isoformat(), search_term),
    )
    exec_id = cur.lastrowid
    conn.commit()
    conn.close()
    return exec_id


def finish_execution(exec_id: int, total_found: int, total_sent: int, total_skipped: int, status: str = "completed"):
    conn = get_connection()
    conn.execute(
        """UPDATE execution_log
           SET finished_at = ?, total_found = ?, total_sent = ?, total_skipped = ?, status = ?
           WHERE id = ?""",
        (datetime.now().isoformat(), total_found, total_sent, total_skipped, status, exec_id),
    )
    conn.commit()
    conn.close()


def get_execution_history(limit: int = 20) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM execution_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_total_sent() -> int:
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) as cnt FROM sent_messages").fetchone()
    conn.close()
    return row["cnt"] if row else 0


init_db()
