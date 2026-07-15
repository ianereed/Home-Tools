"""ONE-SHOT (2026-07-14, evening): outbound rewrite Jul 17-22 + Killer Queen tickets.

  1. Itinerary — rewrite Jul 17-21 day rows (cols C-I) and Jul 22 (cols C,D,E,H only;
     keeps its sleep/plan/todo) for the new routing: RWC → North Tahoe (CA-89 N) →
     Great Basin (Wheeler Peak CG) → across-Utah options → CO high country →
     Nederland → Boulder. No more Incline Village / Reno / Moab.
  2. Itinerary — update the stale "FIXED | Jul 17" constraint text.
  3. Itinerary — mark Killer Queen tickets purchased on the Jul 30 row.
  4. Reservations — ✅ TICKETS PURCHASED on the Killer Queen row; ❌ CANCELLED on
     Tahoe Shakespeare, Truckee-Tahoe Pet Lodge, Wanderlust Mutts (Moab).
  5. DAY OPTIONS — "Thu Jul 30" Boulder-evenings row gets "— 🎫 TICKETS IN HAND".

Idempotent (sentinel checks per step), throttled ~2 s/write, rows located by content.
Run BEFORE the full rebuild. Then move to retired/.
"""
import time

import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)


def wbatch(ws, data):
    time.sleep(2.0)
    return ws.batch_update(data, value_input_option="USER_ENTERED")


def A(vals, i, c=0):
    return (vals[i][c] if len(vals[i]) > c else "").strip()


itin = sh.worksheet("Itinerary")
iv = itin.get_all_values()

# ── 1 · day rows ────────────────────────────────────────────────────────────────
FULL = {  # cols C..I
 "Jul 17": ["Redwood City", "~230", "4", "Truckee — CA-89 N corridor (van)",
   "Surgery appt AM → drive to North Tahoe | camp on the 89-N corridor (pick spot on iOverlander)",
   "Appt fixed — rolling by ~10:30 AM. Friday Sierra traffic: 4–6 hr door-to-door. "
   "Groceries in Truckee. ⚠️ Check Elephant Fire status before committing to the 89-N "
   "corridor — alternates on the day tab.", ""],
 "Jul 18": ["CA-89 N camp (Truckee)", "~410", "6.5", "Great Basin NP — Wheeler Peak CG (9,886 ft)",
   "US-50 'Loneliest Road' → Great Basin | sleep COOL at 9,886 ft",
   "Depart 8 AM. Fuel Fernley → Austin → Ely (never pass one below half a tank). "
   "Dogs: campgrounds + roads only, not park trails. Saturday night — campsite "
   "strategy on the day tab.", ""],
 "Jul 19": ["Great Basin — Wheeler Peak CG", "354–537", "6–8",
   "High country — your pick (La Sal / Grand Mesa / Vail Pass)",
   "Across Utah — pick your push: 3 options on the day tab, all sleeping at 9,500–11,000 ft",
   "Decide by Salina (~11:45 AM). Valley floors run ~100°F midday — whatever the "
   "option, finish the climb; don't sleep low.", ""],
 "Jul 20": ["La Sal / Grand Mesa / Shrine Pass camp", "0–272", "0–5",
   "Shrine Pass or Camp Hale area (van)",
   "Converge on the CO high country — or layover if you already pushed to Vail Pass",
   "Camp set by ~3 PM (afternoon thunderstorms are the rhythm up here). Wildfire "
   "check row on the day tab.", ""],
 "Jul 21": ["Shrine Pass / Camp Hale", "0 or ~80–110", "0–2.2",
   "Same camp OR West Magnolia / Gordon Gulch (Nederland)",
   "CHOICE: layover in the high country OR hop to Boulder's backyard (Nederland)",
   "Moving puts you 38 min from the Airbnb and makes Wednesday lazy — if moving, "
   "roll before noon (storms + Clear Creek traffic later).", ""],
}
PARTIAL_J22 = {"C": "West Magnolia (or Vail Pass camp)", "D": "~22 (or ~97)",
               "E": "0.6–1.8",
               "H": "Down Boulder Canyon ~10 AM, groceries + Chautauqua meadow walk + "
                    "lunch, Airbnb check-in 3 PM (message Kendal ahead). Fill the tank "
                    "on 28th St this afternoon."}

data, seen = [], []
for i in range(len(iv)):
    a = A(iv, i)
    if a in FULL:
        data.append({"range": f"C{i+1}:I{i+1}", "values": [FULL[a]]})
        seen.append(a)
    elif a == "Jul 22":
        data.append({"range": f"C{i+1}:E{i+1}",
                     "values": [[PARTIAL_J22["C"], PARTIAL_J22["D"], PARTIAL_J22["E"]]]})
        data.append({"range": f"H{i+1}", "values": [[PARTIAL_J22["H"]]]})
        seen.append(a)
assert sorted(seen) == sorted(list(FULL) + ["Jul 22"]), f"rows found: {seen}"
wbatch(itin, data)
print(f"1. Itinerary day rows: rewrote {len(seen)} rows (Jul 17–22).")

# ── 2 · constraint row ──────────────────────────────────────────────────────────
iv = itin.get_all_values()
NEWC = ("Surgery appt in Sunnyvale first thing — on the road by ~10:30 AM; "
        "North Tahoe (CA-89 N camp) that night.")
r = next(i for i in range(len(iv)) if A(iv, i) == "FIXED" and A(iv, i, 1) == "Jul 17")
if A(iv, r, 2) == NEWC:
    print("2. Constraint Jul 17: already updated — skipping.")
else:
    wbatch(itin, [{"range": f"C{r+1}", "values": [[NEWC]]}])
    print(f"2. Constraint Jul 17 (row {r+1}): text updated.")

# ── 3 · Jul 30 Killer Queen tickets ─────────────────────────────────────────────
iv = itin.get_all_values()
r30 = next(i for i in range(len(iv)) if A(iv, i) == "Jul 30")
row = iv[r30] + [""] * 9
if "🎫" in row[7]:
    print("3. Jul 30: ticket note already present — skipping.")
else:
    upd = []
    h = (row[7].rstrip() + " " if row[7].strip() else "") + \
        "🎫 Killer Queen tickets PURCHASED ✓ (Red Rocks, evening — pairs with the Golden day trip)."
    upd.append({"range": f"H{r30+1}", "values": [[h]]})
    todo = row[8]
    if "killer queen" in todo.lower() or "red rocks" in todo.lower():
        upd.append({"range": f"I{r30+1}", "values": [[""]]})
    wbatch(itin, upd)
    print(f"3. Jul 30 (row {r30+1}): ticket note added"
          f"{' + buy-todo cleared' if len(upd) > 1 else ''}.")

# ── 4 · Reservations ────────────────────────────────────────────────────────────
res = sh.worksheet("Reservations")
rv = res.get_all_values()
CANCEL = ["Buy Lake Tahoe Shakespeare Festival tickets",
          "Register Mochi at Truckee-Tahoe Pet Lodge",
          "Book Wanderlust Mutts"]
CPREFIX = ("❌ CANCELLED 2026-07-14 — outbound rerouted (no Tahoe layover / no Moab); "
           "driving straight through to Boulder Jul 17–22.")
data, done = [], 0
for i in range(len(rv)):
    b = A(rv, i, 1)
    f = A(rv, i, 5)
    if b.startswith("Buy Red Rocks Killer Queen tickets"):
        if f.startswith("✅"):
            done += 1
        else:
            data.append({"range": f"F{i+1}", "values": [[
                "✅ TICKETS PURCHASED (user confirmed 2026-07-14)."
                + ((" | " + f) if f else "")]]})
    elif any(b.startswith(m) for m in CANCEL):
        if f.startswith("❌ CANCELLED"):
            done += 1
        else:
            data.append({"range": f"F{i+1}",
                         "values": [[CPREFIX + ((" | " + f) if f else "")]]})
if data:
    wbatch(res, data)
print(f"4. Reservations: {len(data)} rows marked ({done} already done).")

# ── 5 · DAY OPTIONS Boulder-evenings Jul 30 row ────────────────────────────────
# GOTCHA (hit on the first run): evenings rows are merged A:B (label) + C:J (text),
# so the text cell is the C-column merge ANCHOR. The original version wrote to B —
# a non-anchor merged cell — and Sheets silently swallowed it (see memory
# project_colorado_trip_day_tabs "stale horizontal merges"). Fixed to locate the
# text cell by content instead of assuming a column.
menu = sh.worksheet("DAY OPTIONS")
mv = menu.get_all_values()
r = next(i for i in range(len(mv)) if A(mv, i) == "Thu Jul 30")
ci = next(c for c in range(1, len(mv[r]) + 1)
          if "Killer Queen" in A(mv, r, c) or "🎫" in A(mv, r, c))
txt = A(mv, r, ci)
if "🎫" in txt:
    print("5. DAY OPTIONS: ticket note already present — skipping.")
else:
    col = chr(ord("A") + ci)
    wbatch(menu, [{"range": f"{col}{r+1}",
                   "values": [[txt + " — 🎫 TICKETS IN HAND"]]}])
    print(f"5. DAY OPTIONS ({col}{r+1}): appended ticket note.")

print("DONE — outbound one-shot complete. Now splice tabs + run rebuild_trip_tabs.py")
