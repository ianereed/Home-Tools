"""SQLite schema and connection helpers."""
import sqlite3

import config


def get_connection() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS transactions (
            id           TEXT PRIMARY KEY,
            date         TEXT NOT NULL,
            payee        TEXT NOT NULL,
            outflow      REAL NOT NULL,
            inflow       REAL NOT NULL,
            amount       REAL NOT NULL,
            category     TEXT,
            account      TEXT,
            memo         TEXT,
            cleared      TEXT,
            is_transfer  INTEGER NOT NULL DEFAULT 0,
            source       TEXT NOT NULL DEFAULT 'ynab_csv',
            raw_file     TEXT,
            imported_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS documents (
            id             TEXT PRIMARY KEY,
            filename       TEXT NOT NULL,
            doc_type       TEXT,
            date_of_doc    TEXT,
            extracted_text TEXT NOT NULL,
            page_count     INTEGER,
            imported_at    TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_txn_date     ON transactions(date);
        CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category);
        CREATE INDEX IF NOT EXISTS idx_txn_xfer     ON transactions(is_transfer);

        CREATE TABLE IF NOT EXISTS budget_months (
            month          TEXT NOT NULL,
            category_id    TEXT NOT NULL,
            category_name  TEXT NOT NULL,
            category_group TEXT,
            budgeted       REAL NOT NULL,
            activity       REAL NOT NULL,
            balance        REAL NOT NULL,
            fetched_at     TEXT NOT NULL,
            PRIMARY KEY (month, category_id)
        );

        CREATE INDEX IF NOT EXISTS idx_bm_month ON budget_months(month);

        CREATE TABLE IF NOT EXISTS sync_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {config.DB_PATH}")
