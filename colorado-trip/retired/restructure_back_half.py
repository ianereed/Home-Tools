"""ONE-SHOT (2026-07-14): restructure the sheet's back half after the Steamboat /
Twin Lakes / Crested Butte / SLC / Ely cancellation.

  1. DAY OPTIONS  — delete the STEAMBOAT + CRESTED BUTTE menu sections (BLD + MAM stay).
  2. Itinerary    — drop the 3 Steamboat/CB CONTEXT constraint rows; add the new
                    "FIXED Aug 5 — home by evening" constraint.
  3. Itinerary    — rewrite Aug 1–5 as drive-home days, Aug 6–13 as HOME rows
                    (cols C–Q; date + dow stay so PHASE 3b re-links date cells).
  4. Itinerary    — insert an OUT OF SCOPE divider above Aug 14 (Aug 14+ untouched:
                    Mammoth + Rae Lakes still happen, planned outside this sheet).
  5. Reservations — prepend "❌ CANCELLED 2026-07-14 …" to col F of the 15
                    Steamboat/CB-dependent rows (rows kept for audit trail).

Idempotent: every step re-checks live state and skips if already applied.
Throttled ~2 s/write (Sheets 60 writes/min quota). Run once, then move to retired/.
Run me BEFORE the full rebuild_trip_tabs.py run (which rewires date-cell links).
"""
import time

import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

_obu = sh.batch_update
def _bu(*a, **k):
    time.sleep(2.0)
    return _obu(*a, **k)
sh.batch_update = _bu


def wbatch(ws, data):
    """values.batchUpdate (1 write request), throttled."""
    time.sleep(2.0)
    return ws.batch_update(data, value_input_option="USER_ENTERED")


def col_a(vals, i):
    return (vals[i][0] if len(vals[i]) > 0 else "").strip()


def col_b(vals, i):
    return (vals[i][1] if len(vals[i]) > 1 else "").strip()


# ── 1 · DAY OPTIONS — delete STEAMBOAT + CB sections ────────────────────────────
menu = sh.worksheet("DAY OPTIONS")
mv = menu.get_all_values()
r_stm = r_mam = None
for i in range(len(mv)):
    a = col_a(mv, i)
    if a.startswith("STEAMBOAT") and "DAY MENU" in a:
        r_stm = i          # 0-based
    if a.startswith("MAMMOTH LAKES") and "DAY MENU" in a:
        r_mam = i
if r_stm is None:
    print("1. DAY OPTIONS: STEAMBOAT section not found — already removed, skipping.")
else:
    assert r_mam is not None and r_mam > r_stm, (r_stm, r_mam)
    sh.batch_update({"requests": [{"deleteDimension": {"range": {
        "sheetId": menu.id, "dimension": "ROWS",
        "startIndex": r_stm, "endIndex": r_mam}}}]})
    print(f"1. DAY OPTIONS: deleted rows {r_stm+1}–{r_mam} (STM + CB sections).")

# ── 2 · Itinerary constraint rows ────────────────────────────────────────────────
itin = sh.worksheet("Itinerary")
iv = itin.get_all_values()
DROP_B = {"Steamboat Aug 1-6", "Crested Butte Aug 9-12", "Crested Butte"}
drop = [i for i in range(len(iv))
        if col_a(iv, i) == "CONTEXT" and col_b(iv, i) in DROP_B]
if not drop:
    print("2a. Constraints: Steamboat/CB CONTEXT rows not found — skipping.")
else:
    reqs = [{"deleteDimension": {"range": {"sheetId": itin.id, "dimension": "ROWS",
                                           "startIndex": i, "endIndex": i + 1}}}
            for i in sorted(drop, reverse=True)]
    sh.batch_update({"requests": reqs})
    print(f"2a. Constraints: deleted {len(drop)} CONTEXT rows: {[i+1 for i in drop]}")

iv = itin.get_all_values()
if any(col_a(iv, i) == "FIXED" and col_b(iv, i) == "Aug 5" for i in range(len(iv))):
    print("2b. Constraints: FIXED Aug 5 row already present — skipping.")
else:
    anchor = next(i for i in range(len(iv))
                  if col_a(iv, i) == "FIXED" and col_b(iv, i) == "Aug 19")
    sh.batch_update({"requests": [{"insertDimension": {
        "range": {"sheetId": itin.id, "dimension": "ROWS",
                  "startIndex": anchor + 1, "endIndex": anchor + 2},
        "inheritFromBefore": True}}]})
    wbatch(itin, [{"range": f"A{anchor+2}:C{anchor+2}", "values": [[
        "FIXED", "Aug 5",
        "Must arrive home in Redwood City by evening (fallback: Aug 6 by ~noon). "
        "The driving phase of the trip ends here."]]}])
    print(f"2b. Constraints: inserted FIXED Aug 5 row at row {anchor+2}.")

# ── 3 · Itinerary day rows (cols C–Q; A/B untouched) ────────────────────────────
#     C wake, D miles, E hrs, F sleep, G plan, H notes, I todo, J–Q cleared
D = {
 "Aug 1": ["Boulder AirBNB", "192", "3.8", "Saratoga, WY (van)",
   "Drive day 1 — Boulder → Snowy Range Scenic Byway → Saratoga | Hobo Hot Pool",
   "Checkout 11 AM. US-287 to Laramie, WY-130 over Snowy Range Pass (10,847 ft — "
   "Lake Marie leg-stretch, ~65°F), down to Saratoga: free 24/7 Hobo Hot Pool + "
   "North Platte swim for Mochi. Leg-by-leg on the day tab.", ""],
 "Aug 2": ["Saratoga, WY (van)", "284", "4.4", "Uintas — Bear River corridor (van)",
   "Drive day 2 — Saratoga → I-80 W → Mirror Lake Hwy (Uinta Mtns) camp",
   "I-80 through Rawlins → Rock Springs → Evanston, then 30 min up UT-150 to a "
   "~8,400–9,000 ft USFS camp. Optional Flaming Gorge overlook detour (+~100 mi). "
   "Cold, quiet night.", ""],
 "Aug 3": ["Uintas (van)", "286", "5.0", "Angel Lake, NV (van)",
   "Drive day 3 — Mirror Lake Hwy → Park City lunch → Bonneville → Angel Lake",
   "AM Ruth Lake hike (1.5 mi RT, 10,300 ft) + Bald Mtn Pass + Provo River Falls; "
   "Park City lunch; I-80 past SLC + Salt Flats photo stop; up NV-231 to Angel Lake "
   "(8,380 ft cirque above Wells).", ""],
 "Aug 4": ["Angel Lake (van)", "246", "4.4", "Winnemucca, NV (van)",
   "Drive day 4 — Ruby Mountains: Lamoille Canyon + Lamoille Lake hike → Winnemucca",
   "Easy day: Lamoille Canyon Scenic Byway (12-mi glacial canyon) + Lamoille Lake "
   "(3 mi RT, 9,700 ft, dogs OK), then I-80 to Winnemucca. Water Canyon Rec Area "
   "above town = cooler BLM sleep option.", ""],
 "Aug 5": ["Winnemucca (van)", "432", "6.9", "🏠 HOME — Redwood City",
   "Drive day 5 — Winnemucca → Truckee / Kings Beach dog swim → HOME by evening",
   "The one long day. Decision point at Truckee ~1 PM: feeling good → Mochi swims at "
   "Kings Beach dog beach + lunch, home ~6–7 PM. Tired → overnight Truckee/Donner, "
   "home Aug 6 by ~noon.", ""],
 "Aug 6": ["Home — Redwood City", "0", "0", "Home — Redwood City",
   "🏠 Home (fallback: arrive by ~noon if you overnighted at Truckee)", "", ""],
}
for date in ["Aug 7", "Aug 8", "Aug 9", "Aug 10", "Aug 11", "Aug 12", "Aug 13"]:
    D[date] = ["Home — Redwood City", "0", "0", "Home — Redwood City", "🏠 Home", "", ""]

iv = itin.get_all_values()
data, seen = [], []
for i in range(len(iv)):
    a = col_a(iv, i)
    if a in D:
        data.append({"range": f"C{i+1}:Q{i+1}", "values": [D[a] + [""] * 8]})
        seen.append(a)
missing = [d for d in D if d not in seen]
assert not missing, f"Itinerary rows not found for: {missing}"
wbatch(itin, data)
print(f"3. Day rows: rewrote {len(data)} rows (Aug 1–13).")

# ── 4 · OUT OF SCOPE divider above Aug 14 ────────────────────────────────────────
SENTINEL = "OUT OF SCOPE from here down"
iv = itin.get_all_values()
if any(SENTINEL in (iv[i][2] if len(iv[i]) > 2 else "") for i in range(len(iv))):
    print("4. Divider: already present — skipping.")
else:
    r14 = next(i for i in range(len(iv)) if col_a(iv, i) == "Aug 14")
    sh.batch_update({"requests": [{"insertDimension": {
        "range": {"sheetId": itin.id, "dimension": "ROWS",
                  "startIndex": r14, "endIndex": r14 + 1},
        "inheritFromBefore": False}}]})
    wbatch(itin, [{"range": f"C{r14+1}", "values": [[
        "⬇ OUT OF SCOPE from here down — Mammoth (Aug 14–18) + Rae Lakes "
        "(Aug 19–24) are still happening, but are planned outside this sheet now "
        "(rows kept as-is, no longer actively edited)."]]}])
    print(f"4. Divider: inserted above Aug 14 (new row {r14+1}).")

# ── 5 · Reservations — mark the Steamboat/CB set CANCELLED ───────────────────────
res = sh.worksheet("Reservations")
rv = res.get_all_values()
MATCH = [
    "Book Steamboat Springs Airbnb", "Book Crested Butte Airbnb",
    "Book Soupçon dinner", "Call Red Rover Resort", "Call Oh Be Dogful",
    "Call Dolly's Mountain Shuttle", "Check Strings Music Festival schedule (Steamboat",
    "Book Maroon Bells timed entry", "Book Aurum Food & Wine dinner",
    "Buy Steamboat Pro Rodeo Series tickets", "Call Ride Workshop",
    "Book Strawberry Park Hot Springs", "WEST MAROON PASS point-to-point",
    "Book Maroon Bells RFTA bus", "Arrange Maroon Bells Shuttles",
]
PREFIX = ("❌ CANCELLED 2026-07-14 — Steamboat/CB legs cancelled; "
          "driving home Aug 1–5 instead.")
data, hit = [], []
for i in range(len(rv)):
    b = (rv[i][1] if len(rv[i]) > 1 else "").strip()
    f = (rv[i][5] if len(rv[i]) > 5 else "").strip()
    if not any(b.startswith(m) for m in MATCH):
        continue
    hit.append(b[:50])
    if f.startswith("❌ CANCELLED"):
        continue
    data.append({"range": f"F{i+1}",
                 "values": [[PREFIX + ((" | " + f) if f else "")]]})
if len(hit) != len(MATCH):
    print(f"  ⚠️ expected {len(MATCH)} reservation rows, matched {len(hit)}: {hit}")
if data:
    wbatch(res, data)
print(f"5. Reservations: marked {len(data)} rows CANCELLED "
      f"({len(hit)} matched, {len(hit)-len(data)} already marked).")

print("DONE — one-shot complete. Now run: python3 rebuild_trip_tabs.py")
