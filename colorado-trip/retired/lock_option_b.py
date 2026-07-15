"""ONE-SHOT (2026-07-14, late): lock Jul 19 push = Option B (Grand Mesa).

  1. Itinerary — Jul 19 row committed to Grand Mesa; Jul 20 row updated to the
     Mesa→Camp Hale/Vail Pass day (Crag Crest AM).
  2. Reservations — add "Book Island Lake CG (rec.gov 233387)" to the first empty
     row (no row inserts → no merge/shift risk).

Idempotent; throttled; rows located by content. Run once, then move to retired/.
(The Jul 19/20 day TABS are rebuilt separately via the single-tab workshop path.)
"""
import time

import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
sh = gspread.authorize(creds).open_by_key(SPREADSHEET_ID)


def wbatch(ws, data):
    time.sleep(2.0)
    return ws.batch_update(data, value_input_option="USER_ENTERED")


def A(vals, i, c=0):
    return (vals[i][c] if len(vals[i]) > c else "").strip()


itin = sh.worksheet("Itinerary")
iv = itin.get_all_values()
ROWS = {
 "Jul 19": {"D": "430", "E": "6.8",
   "F": "Grand Mesa — Island Lake CG or dispersed (10,000 ft)",
   "G": "Across Utah → GRAND MESA (locked 7/14 — was a 3-option day)",
   "H": "Reserve Island Lake CG from the road (rec.gov 233387 — 9 Sunday sites open "
        "as of Jul 15). GJ valley ~95°F midday; the Mesa is clear of fires (GMUG's "
        "two are 90 mi south — glance at alerts before the climb). Bug spray."},
 "Jul 20": {"C": "Grand Mesa (van)", "D": "165", "E": "3.0",
   "G": "Crag Crest taste (AM) → Glenwood Canyon → Camp Hale / Vail Pass high country",
   "H": "Camp set by ~3 PM (afternoon storms). ⚠️ Willow Fire: no camps west of "
        "Leadville; US-24 open — verify on InciWeb. Zero-drama night: reserve Camp "
        "Hale Memorial (rec.gov 232274, 16 Monday sites open as of Jul 15)."},
}
data, seen = [], []
for i in range(len(iv)):
    a = A(iv, i)
    if a in ROWS:
        for col, val in ROWS[a].items():
            data.append({"range": f"{col}{i+1}", "values": [[val]]})
        seen.append(a)
assert sorted(seen) == ["Jul 19", "Jul 20"], seen
wbatch(itin, data)
print(f"1. Itinerary: updated {seen} ({len(data)} cells).")

res = sh.worksheet("Reservations")
rv = res.get_all_values()
TASK = "Book Island Lake CG — Grand Mesa, Sun Jul 19 night (rec.gov 233387) !!1 Jul 15"
if any(A(rv, i, 1).startswith("Book Island Lake CG") for i in range(len(rv))):
    print("2. Reservations: Island Lake row already present — skipping.")
else:
    empty = next(i for i in range(4, len(rv)) if not A(rv, i, 1))
    wbatch(res, [{"range": f"B{empty+1}:F{empty+1}", "values": [[
        TASK, "Camping", "Jul 15",
        "recreation.gov/camping/campgrounds/233387",
        "Option B locked 2026-07-14. 9 of 11 reservable Sunday sites open as of the "
        "Jul 15 snapshot — book now. Fallbacks: Cobbett Lake (233936), Jumbo (233189), "
        "or legal dispersed along the Mesa forest roads."]]}])
    print(f"2. Reservations: Island Lake booking row written at row {empty+1}.")

print("DONE — now rebuild the two day tabs (single-tab workshop) + rewire links.")
