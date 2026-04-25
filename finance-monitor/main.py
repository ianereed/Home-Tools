"""
finance-monitor CLI entry points.

Usage:
  python main.py watch         # scan intake/ and import new files (run by LaunchAgent)
  python main.py serve         # start Slack bot (KeepAlive LaunchAgent)
  python main.py sync          # run one YNAB API sync (testing / manual catch-up)
  python main.py ask "<q>"     # answer a question from the command line (testing)
  python main.py stats         # print DB row counts
"""
import sys

import db


def _cmd_watch():
    import watcher
    watcher.run()


def _cmd_serve():
    import slack_bot
    slack_bot.run()


def _cmd_sync():
    db.init_db()
    from ingest import ynab_api
    status = ynab_api.sync()
    print(status)


def _cmd_ask(question: str):
    import query_engine
    print(query_engine.answer(question))


def _cmd_stats():
    db.init_db()
    conn = db.get_connection()
    txn_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    xfer_count = conn.execute("SELECT COUNT(*) FROM transactions WHERE is_transfer=1").fetchone()[0]
    oldest = conn.execute("SELECT MIN(date) FROM transactions WHERE is_transfer=0").fetchone()[0]
    newest = conn.execute("SELECT MAX(date) FROM transactions WHERE is_transfer=0").fetchone()[0]
    by_source = conn.execute(
        "SELECT source, COUNT(*) AS n FROM transactions GROUP BY source ORDER BY n DESC"
    ).fetchall()
    month_count = conn.execute("SELECT COUNT(DISTINCT month) FROM budget_months").fetchone()[0]
    conn.close()
    print(f"Transactions:  {txn_count} total ({xfer_count} transfers excluded from queries)")
    for r in by_source:
        print(f"  by source:   {r['source']:<10} {r['n']}")
    print(f"Date range:    {oldest} → {newest}")
    print(f"Budget months: {month_count} snapshotted")
    print(f"Documents:     {doc_count}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "watch":
        _cmd_watch()
    elif cmd == "serve":
        _cmd_serve()
    elif cmd == "sync":
        _cmd_sync()
    elif cmd == "ask":
        if len(sys.argv) < 3:
            print("Usage: python main.py ask \"<question>\"")
            sys.exit(1)
        _cmd_ask(sys.argv[2])
    elif cmd == "stats":
        _cmd_stats()
    else:
        print(f"Unknown command: {cmd!r}")
        print(__doc__)
        sys.exit(1)
