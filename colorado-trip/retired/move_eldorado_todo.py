"""
move_eldorado_todo.py — one-shot (2026-07-15): Eldorado Canyon is now OPTIONAL and the
Jul 27 slot it was pinned to is a van-free (Geotrek) day.

1. Itinerary: clear the "Book Eldorado Canyon timed entry" todo from the Jul 27 row.
2. Itinerary: add an OPTIONAL-framed version to the Jul 23 row (first flexible Boulder
   day): weekdays need NO timed entry, only weekends (Jul 25/26) do; if it happens at
   all, easiest after the van returns (Jul 30–31).
3. Reservations: prepend an OPTIONAL status note to the Eldorado row's Notes col (F) —
   its old note targeted "Jul 26–28", which now collides with the van-free days.

Idempotent (sentinel checks per step), throttled ~2 s/write, rows located by content.
"""
import time

import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE

gc = gspread.service_account(filename=CREDENTIALS_FILE)
sh = gc.open_by_key(SPREADSHEET_ID)

def W(fn, *a, **k):
    time.sleep(2.0)
    return fn(*a, **k)

# ── 1 + 2. Itinerary todo move ────────────────────────────────────────────────────
it = sh.worksheet("Itinerary")
vals = it.get_all_values()
hdr = next(r for r in vals if r and r[0].strip() == "Date")
todo_col = hdr.index("Todo")            # 0-based

NEW_TODO = ("OPTIONAL — Eldorado (BLD-F) is a maybe now, and needs the van. Weekday "
            "visits need NO timed entry; a WEEKEND visit (Jul 25/26) needs the free "
            "cpwshop.com slot (+$10 gate; 15-day window already open). Jul 27–29 are "
            "van-free — if it happens, easiest Jul 30–31.")

r27 = next(i for i, r in enumerate(vals) if r and r[0].strip() == "Jul 27")
cur27 = vals[r27][todo_col].strip() if len(vals[r27]) > todo_col else ""
if "Eldorado" in cur27:
    W(it.update_cell, r27 + 1, todo_col + 1, "")
    print(f"Jul 27 (r{r27+1}): cleared todo {cur27!r}")
else:
    print(f"Jul 27 (r{r27+1}): no Eldorado todo present ({cur27!r}) — skip.")

r23 = next(i for i, r in enumerate(vals) if r and r[0].strip() == "Jul 23")
cur23 = vals[r23][todo_col].strip() if len(vals[r23]) > todo_col else ""
if "Eldorado" in cur23:
    print(f"Jul 23 (r{r23+1}): todo already present — skip.")
else:
    assert cur23 == "", f"Jul 23 todo cell unexpectedly non-empty: {cur23!r} — aborting."
    W(it.update_cell, r23 + 1, todo_col + 1, NEW_TODO)
    print(f"Jul 23 (r{r23+1}): todo written.")

# ── 3. Reservations status note ───────────────────────────────────────────────────
res = sh.worksheet("Reservations")
rvals = res.get_all_values()
ri = next(i for i, r in enumerate(rvals) if len(r) > 1 and "Eldorado" in r[1])
F = rvals[ri][5] if len(rvals[ri]) > 5 else ""
SENT = "⬜ OPTIONAL 2026-07-15"
if F.startswith(SENT):
    print(f"Reservations r{ri+1}: already marked OPTIONAL — skip.")
else:
    newf = (f"{SENT} — may skip Eldorado entirely. Weekdays need NO timed entry "
            f"(only summer weekends/holidays do); old target 'Jul 26–28' is stale — "
            f"Jul 27–29 are van-free (Geotrek). If going: weekend Jul 25/26 needs the "
            f"free slot (window open now), else just go Jul 30–31. | {F}")
    W(res.update_cell, ri + 1, 6, newf)
    print(f"Reservations r{ri+1}: Notes prefixed with OPTIONAL status.")

print("DONE.")
