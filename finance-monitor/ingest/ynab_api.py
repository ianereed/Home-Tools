"""
YNAB API sync — READ-ONLY.

HARD RULE: this module MUST NEVER call a write endpoint (POST/PUT/PATCH/DELETE)
against the YNAB API. The YnabClient below exposes only .get(). Do not add
other HTTP verbs to this client or import `requests` write methods elsewhere
in this file.

Sync flow:
  1. One-time cleanup: delete CSV-imported transactions dated >= YNAB_API_CUTOFF
     so the API becomes the sole source of truth from the cutoff forward.
  2. Delta-fetch transactions via `last_knowledge_of_server`; on first run,
     backfill since YNAB_API_CUTOFF.
  3. Snapshot the current (and, on month rollover, prior) monthly budget.

Called from watcher.run() every 5 minutes. Never raises.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import requests

import config
import db

logger = logging.getLogger(__name__)

_TIMEOUT = 30


class YnabClient:
    """READ-ONLY YNAB API client. Exposes only GET. Do not add write methods."""

    _BASE_URL = "https://api.ynab.com/v1"

    def __init__(self, token: str):
        self._headers = {"Authorization": f"Bearer {token}"}

    def get(self, path: str, params: dict | None = None) -> dict:
        resp = requests.get(
            f"{self._BASE_URL}{path}",
            headers=self._headers,
            params=params,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["data"]


# ── sync_state helpers ────────────────────────────────────────────────────────

def _state_get(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _state_set(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO sync_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# ── Budget ID resolution ──────────────────────────────────────────────────────

def _resolve_budget_id(client: YnabClient, conn) -> str:
    if config.YNAB_BUDGET_ID:
        return config.YNAB_BUDGET_ID

    cached = _state_get(conn, "ynab_budget_id")
    if cached:
        return cached

    data = client.get("/budgets")
    budgets = data.get("budgets", [])
    if not budgets:
        raise RuntimeError("YNAB account has no budgets")
    if len(budgets) > 1:
        listing = ", ".join(f"{b['name']!r}={b['id']}" for b in budgets)
        raise RuntimeError(
            f"YNAB account has multiple budgets — set YNAB_BUDGET_ID in .env. Found: {listing}"
        )

    budget_id = budgets[0]["id"]
    _state_set(conn, "ynab_budget_id", budget_id)
    conn.commit()
    logger.info("ynab_api: auto-discovered budget id %s (name=%r)", budget_id, budgets[0]["name"])
    return budget_id


# ── Transaction upsert / delete ───────────────────────────────────────────────

def _milliunits_to_dollars(milliunits: int) -> float:
    return round(milliunits / 1000.0, 2)


def _upsert_transaction(conn, txn: dict[str, Any], now_iso: str) -> None:
    amount_milli = txn.get("amount", 0) or 0
    amount = _milliunits_to_dollars(amount_milli)
    outflow = -amount if amount < 0 else 0.0
    inflow = amount if amount > 0 else 0.0
    is_transfer = 1 if txn.get("transfer_account_id") else 0

    conn.execute(
        """INSERT INTO transactions
           (id, date, payee, outflow, inflow, amount, category, account,
            memo, cleared, is_transfer, source, raw_file, imported_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ynab_api', NULL, ?)
           ON CONFLICT(id) DO UPDATE SET
               date=excluded.date, payee=excluded.payee,
               outflow=excluded.outflow, inflow=excluded.inflow, amount=excluded.amount,
               category=excluded.category, account=excluded.account,
               memo=excluded.memo, cleared=excluded.cleared,
               is_transfer=excluded.is_transfer, imported_at=excluded.imported_at""",
        (
            txn["id"],
            txn["date"],
            (txn.get("payee_name") or "").strip(),
            outflow,
            inflow,
            amount,
            txn.get("category_name"),
            txn.get("account_name"),
            txn.get("memo"),
            txn.get("cleared"),
            is_transfer,
            now_iso,
        ),
    )


def _delete_transaction(conn, txn_id: str) -> None:
    conn.execute(
        "DELETE FROM transactions WHERE id = ? AND source = 'ynab_api'",
        (txn_id,),
    )


# ── Monthly budget snapshot ───────────────────────────────────────────────────

def _snapshot_month(client: YnabClient, conn, budget_id: str, month: str, now_iso: str) -> int:
    """Fetch a single month and upsert all its categories. Returns row count."""
    data = client.get(f"/budgets/{budget_id}/months/{month}")
    categories = data.get("month", {}).get("categories", [])
    count = 0
    for cat in categories:
        if cat.get("deleted"):
            continue
        conn.execute(
            """INSERT OR REPLACE INTO budget_months
               (month, category_id, category_name, category_group,
                budgeted, activity, balance, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                month,
                cat["id"],
                cat["name"],
                cat.get("category_group_name"),
                _milliunits_to_dollars(cat.get("budgeted", 0) or 0),
                _milliunits_to_dollars(cat.get("activity", 0) or 0),
                _milliunits_to_dollars(cat.get("balance", 0) or 0),
                now_iso,
            ),
        )
        count += 1
    return count


def _prior_month(month: str) -> str:
    """'2026-05-01' → '2026-04-01'."""
    y, m, _ = month.split("-")
    y_i, m_i = int(y), int(m)
    if m_i == 1:
        return f"{y_i - 1:04d}-12-01"
    return f"{y_i:04d}-{m_i - 1:02d}-01"


def _current_month_key() -> str:
    today = date.today()
    return today.replace(day=1).isoformat()


def _cutoff_month_key() -> str:
    y, m, _ = config.YNAB_API_CUTOFF.split("-")
    return f"{y}-{m}-01"


# ── One-time CSV cleanup ──────────────────────────────────────────────────────

def _maybe_cleanup_csv_rows(conn) -> int:
    """Run once: delete CSV-imported rows dated >= YNAB_API_CUTOFF."""
    if _state_get(conn, "ynab_csv_cleanup_done") == "1":
        return 0
    cur = conn.execute(
        "DELETE FROM transactions WHERE source = 'ynab_csv' AND date >= ?",
        (config.YNAB_API_CUTOFF,),
    )
    deleted = cur.rowcount
    _state_set(conn, "ynab_csv_cleanup_done", "1")
    logger.info(
        "ynab_api: one-time CSV cleanup — deleted %d ynab_csv rows with date >= %s",
        deleted, config.YNAB_API_CUTOFF,
    )
    return deleted


# ── Public entry point ────────────────────────────────────────────────────────

def sync() -> dict:
    """
    Perform one YNAB sync cycle. Returns a status dict. Never raises.

    Status keys on success: txn_upserts, txn_deletes, months_snapshotted,
                            csv_deleted (only on first successful run).
    Status keys on failure: error (string).
    Status keys when skipped: skipped (string).
    """
    if not config.YNAB_API_TOKEN:
        return {"skipped": "YNAB_API_TOKEN not set"}

    try:
        client = YnabClient(config.YNAB_API_TOKEN)
        conn = db.get_connection()
        try:
            budget_id = _resolve_budget_id(client, conn)
            now_iso = datetime.now(tz=timezone.utc).isoformat()

            csv_deleted = _maybe_cleanup_csv_rows(conn)

            # Transactions — delta if we have a cursor, else backfill since cutoff.
            params: dict[str, Any] = {}
            last_knowledge = _state_get(conn, "ynab_server_knowledge")
            if last_knowledge:
                params["last_knowledge_of_server"] = last_knowledge
            else:
                params["since_date"] = config.YNAB_API_CUTOFF

            txn_data = client.get(f"/budgets/{budget_id}/transactions", params=params)
            upserts = 0
            deletes = 0
            for txn in txn_data.get("transactions", []):
                if txn.get("deleted"):
                    _delete_transaction(conn, txn["id"])
                    deletes += 1
                else:
                    _upsert_transaction(conn, txn, now_iso)
                    upserts += 1

            if "server_knowledge" in txn_data:
                _state_set(conn, "ynab_server_knowledge", str(txn_data["server_knowledge"]))

            # Monthly snapshot — current month always; prior month on rollover.
            current_month = _current_month_key()
            months_done = 0
            rows = _snapshot_month(client, conn, budget_id, current_month, now_iso)
            months_done += 1
            logger.info("ynab_api: snapshotted %s (%d category rows)", current_month, rows)

            last_snapshot_month = _state_get(conn, "ynab_last_snapshot_month")
            if last_snapshot_month and last_snapshot_month != current_month:
                prior = _prior_month(current_month)
                if prior >= _cutoff_month_key():
                    rows = _snapshot_month(client, conn, budget_id, prior, now_iso)
                    months_done += 1
                    logger.info("ynab_api: also snapshotted prior month %s (%d rows)", prior, rows)
            _state_set(conn, "ynab_last_snapshot_month", current_month)

            conn.commit()

            result = {
                "txn_upserts": upserts,
                "txn_deletes": deletes,
                "months_snapshotted": months_done,
            }
            if csv_deleted:
                result["csv_deleted"] = csv_deleted
            return result
        finally:
            conn.close()
    except requests.RequestException as exc:
        logger.error("ynab_api: HTTP error during sync: %s", exc)
        return {"error": f"http: {exc}"}
    except RuntimeError as exc:
        logger.error("ynab_api: sync aborted: %s", exc)
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — sync must never raise
        logger.exception("ynab_api: unexpected error during sync")
        return {"error": f"unexpected: {exc}"}
