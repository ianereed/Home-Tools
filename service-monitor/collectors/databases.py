"""Introspect health.db and finance.db: row counts + freshness."""
import sqlite3
import time
import streamlit as st
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from paths import HEALTH_DB_PATH, FINANCE_DB_PATH


def _info(path, table_queries: dict) -> dict:
    if not path.exists():
        return {"available": False, "reason": "db not found"}
    info = {
        "available": True,
        "size_bytes": path.stat().st_size,
        "mtime_age_sec": int(time.time() - path.stat().st_mtime),
        "tables": {},
    }
    try:
        uri = f"file:{path}?mode=ro&immutable=0"
        conn = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            cur = conn.cursor()
            for name, query in table_queries.items():
                try:
                    cur.execute(query)
                    info["tables"][name] = cur.fetchone()[0]
                except sqlite3.Error as e:
                    info["tables"][name] = f"error: {e}"
        finally:
            conn.close()
    except sqlite3.Error as e:
        info["error"] = str(e)
    return info


@st.cache_data(ttl=10)
def get_health_db() -> dict:
    return _info(HEALTH_DB_PATH, {
        "sleep": "SELECT COUNT(*) FROM sleep",
        "heart_rate": "SELECT COUNT(*) FROM heart_rate",
        "activities": "SELECT COUNT(*) FROM activities",
        "wellness": "SELECT COUNT(*) FROM wellness",
    })


@st.cache_data(ttl=10)
def get_finance_db() -> dict:
    return _info(FINANCE_DB_PATH, {
        "transactions": "SELECT COUNT(*) FROM transactions",
        "documents": "SELECT COUNT(*) FROM documents",
        "budget_months": "SELECT COUNT(*) FROM budget_months",
    })
