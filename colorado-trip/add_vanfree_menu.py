"""
add_vanfree_menu.py — one-shot: add the 🚐 VAN AT GEOTREK (Jul 27–29) car-free rows to
the Boulder section of the DAY OPTIONS menu, point the Jul 27–29 Itinerary notes at
them, and add the Camp Bow Wow Interview-Day row to Reservations (the free interview
must precede any daycare day — it enables the dog-free BLD-N big ride).

The van is at GeoTrek for the A/C + Starlink install Mon Jul 27 – Wed Jul 29, 2026 (back
Thu Jul 30). Those three flexible Boulder days become car-free: this inserts a sub-header
+ four new option rows (BLD-K..N, built by rebuild_trip_tabs.py) directly below the BLD-J
row, INSIDE the Boulder menu, so the existing column header (row 14) still applies. The
BLD-K..N id cells get auto-linked to their option tabs by rebuild_trip_tabs.py PHASE 3.

Idempotent: guards on the sub-header sentinel — re-running overwrites the same block
in place instead of inserting again. Throttled ~2 s/write (Sheets 60 writes/min quota).
Merged-cell rule: rows 14–24 of DAY OPTIONS carry no merges (verified 2026-07-15), and we
unmerge the target range before writing values anyway — a stale A:J merge silently
swallows cols B–J of a values write (bit us 2026-06-01).
"""
import time
import config
import gspread

gc = gspread.service_account(filename=config.CREDENTIALS_FILE)
sh = gc.open_by_key(config.SPREADSHEET_ID)

_orig = gspread.Client.request
def _throttled(self, *a, **k):
    time.sleep(2.0)
    return _orig(self, *a, **k)
gspread.Client.request = _throttled

m = sh.worksheet("DAY OPTIONS")
sid = m.id

def rgb(r, g, b): return {"red": r/255, "green": g/255, "blue": b/255}
AMBER = rgb(146, 64, 14)     # van-days bar — distinct from the Boulder green header
WHITE = rgb(255, 255, 255)

SENTINEL = "🚐 VAN AT GEOTREK"
SUBHDR = ("🚐 VAN AT GEOTREK — CAR-FREE DAYS  (Mon Jul 27 – Wed Jul 29 · Geotrek = 6420 Odell Pl, "
          "Gunbarrel, (720) 640-6001, Mon–Fri 8–5, closed weekends → drop Mon ~8 AM with bikes in "
          "the van + pedal ~6.4 mi home; van back Thu Jul 30 with A/C + Starlink · everything below "
          "runs on bikes + feet from the Airbnb · Mochi: bike trailer or on foot — no RTD (dogs must "
          "be small + in carriers), Uber Pet works in a pinch · same columns as above)")
OPTS = [
    ["FALSE", "BLD-K", "🚐 Together", "🚲 ~8–10 mi",
     "Van-free classic: creek path cruise + Pearl St + a pool swim",
     "Easy town spin (flat)", "Same",
     "Trailer or trots the path — creek splash at Eben G. Fine",
     "None — pool drop-in $15 covers both city pools",
     "Heat → swim AM + patios; storm → Museum of Boulder (closed Tue) / Trident"],
    ["FALSE", "BLD-L", "🚐 Separate", "🚲 Ian 9–17 mi",
     "Van-free separate: Ian pedals to Valmont (or earns Betasso) / Anny + Mochi Wonderland",
     "Valmont session, or Betasso via the Link climb", "Wonderland Lake from the door",
     "AM with Anny (leash); pup zone at the Rayback regroup",
     "None. Mon = Avery CLOSED (Rayback is open + has the pup zone); Betasso loops = no bikes Wed + Sat",
     "Valmont wet → Movement climbing or town day"],
    ["FALSE", "BLD-M", "🚐 On foot", "🚲 ~6–10 mi",
     "Van-free on foot: Sanitas (or Chautauqua) hike + teahouse lunch",
     "Sanitas loop — or pedal to Chautauqua for a Flatirons hike", "Sanitas Valley / meadow walk",
     "Hikes along (leash) — no trailer needed",
     "None",
     "Heat → flip to BLD-K (creek + pool)"],
    ["FALSE", "BLD-N", "🚐 Big ride", "🚲 20–32 mi",
     "Van-free big ride: LoBo Trail to Niwot / Longmont — or the Boulder Valley Ranch gravel loop",
     "LoBo to Left Hand Brewing + back (or BVR gravel from the door)",
     "Same at cruiser pace — or turn around at Niwot",
     "Trailer with shade + water stops, OR a Camp Bow Wow day",
     "Book Camp Bow Wow ahead if riding dog-free (interview day may be required)",
     "Heat/storm → turn at Niwot (~20 mi RT) or BVR loop early AM"],
]

vals = m.get_all_values()
def row_of(pred):
    return next((i for i, r in enumerate(vals) if r and pred(r[0])), None)

existing = row_of(lambda c: c.startswith(SENTINEL))
bldj = next((i for i, r in enumerate(vals) if len(r) > 1 and r[1].strip() == "BLD-J"), None)
assert bldj is not None, "BLD-J row not found in DAY OPTIONS — layout changed, aborting."

NROWS = 2 + len(OPTS)   # spacer + sub-header + 4 option rows
if existing is None:
    start = bldj + 1     # 0-based row index where the block starts (right below BLD-J)
    sh.batch_update({"requests": [{"insertDimension": {
        "range": {"sheetId": sid, "dimension": "ROWS",
                  "startIndex": start, "endIndex": start + NROWS},
        "inheritFromBefore": False}}]})
    print(f"Inserted {NROWS} rows at sheet row {start+1} (below BLD-J).")
else:
    start = existing - 1  # overwrite in place, incl. the spacer above the sentinel
    print(f"Sentinel found at sheet row {existing+1} — overwriting in place.")

PAD = lambda r: (r + [""] * (10 - len(r)))[:10]
block = [PAD([""]), PAD([SUBHDR])] + [PAD(o) for o in OPTS]

# defensive unmerge across the block before writing values (merged-cell swallow gotcha)
sh.batch_update({"requests": [{"unmergeCells": {"range": {"sheetId": sid,
    "startRowIndex": start, "endRowIndex": start + NROWS,
    "startColumnIndex": 0, "endColumnIndex": 10}}}]})

m.update(range_name=f"A{start+1}", values=block, value_input_option="USER_ENTERED")

i_hdr, i_opt0 = start + 1, start + 2
reqs = [
    {"mergeCells": {"range": {"sheetId": sid, "startRowIndex": i_hdr, "endRowIndex": i_hdr+1,
        "startColumnIndex": 0, "endColumnIndex": 10}, "mergeType": "MERGE_ALL"}},
    {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": i_hdr, "endRowIndex": i_hdr+1,
        "startColumnIndex": 0, "endColumnIndex": 10},
        "cell": {"userEnteredFormat": {"backgroundColor": AMBER,
                 "textFormat": {"bold": True, "foregroundColor": WHITE, "fontSize": 10},
                 "horizontalAlignment": "LEFT", "wrapStrategy": "WRAP"}},
        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,wrapStrategy)"}},
    {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": i_opt0, "endRowIndex": i_opt0+len(OPTS),
        "startColumnIndex": 0, "endColumnIndex": 10},
        "cell": {"userEnteredFormat": {"backgroundColor": WHITE,
                 "textFormat": {"bold": False, "fontSize": 9},
                 "horizontalAlignment": "LEFT", "wrapStrategy": "WRAP"}},
        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,wrapStrategy)"}},
    {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS",
        "startIndex": i_hdr, "endIndex": i_hdr+1}, "properties": {"pixelSize": 48}, "fields": "pixelSize"}},
    {"setDataValidation": {"range": {"sheetId": sid, "startRowIndex": i_opt0,
        "endRowIndex": i_opt0+len(OPTS), "startColumnIndex": 0, "endColumnIndex": 1},
        "rule": {"condition": {"type": "BOOLEAN"}}}},
]
sh.batch_update({"requests": reqs})
print(f"Menu block written: sub-header row {i_hdr+1}, BLD-K..N rows {i_opt0+1}–{i_opt0+len(OPTS)}.")

# ── Itinerary: point the Jul 27–29 notes at the new options ──────────────────────
it = sh.worksheet("Itinerary")
ivals = it.get_all_values()
hdr = next(r for r in ivals if r and r[0].strip() == "Date")
notes_col = hdr.index("Notes")          # 0-based
NEW_NOTE = "Van with Geotrek — car-free day: pick a 🚐 option (BLD-K…N) in DAY OPTIONS"
for date in ("Jul 27", "Jul 28", "Jul 29"):
    ri = next(i for i, r in enumerate(ivals) if r and r[0].strip() == date)
    cur = ivals[ri][notes_col].strip() if len(ivals[ri]) > notes_col else ""
    if "BLD-K" in cur:
        print(f"{date}: note already updated — skip.")
        continue
    assert cur == "" or "Geotrek" in cur, f"{date}: unexpected Notes text {cur!r} — aborting before overwrite."
    it.update_cell(ri + 1, notes_col + 1, NEW_NOTE)
    print(f"{date}: Notes (r{ri+1} c{notes_col+1}) -> {NEW_NOTE!r}")

# ── Reservations: Camp Bow Wow Interview Day row (enables dog-free van-days) ──────
res = sh.worksheet("Reservations")
rvals = res.get_all_values()
CBW_TASK = ("Schedule Camp Bow Wow Boulder Interview Day (free) — required before any "
            "daycare day; do it before the Jul 27–29 van-days !!2 Jul 23")
if any(len(r) > 1 and "Camp Bow Wow Boulder Interview" in r[1] for r in rvals):
    print("Reservations: Camp Bow Wow row already present — skip.")
else:
    anchor = next(i for i, r in enumerate(rvals) if len(r) > 1 and "Island Lake CG" in r[1])
    empty = next(i for i in range(anchor + 1, len(rvals))
                 if len(rvals[i]) < 2 or not rvals[i][1].strip())
    row = ["FALSE", CBW_TASK, "Mochi / Daycare", "Jul 23",
           "(720) 605-4733 / campbowwow.com/boulder",
           "3631 Pearl St (4.7 mi pedal from the Airbnb). Free required interview before "
           "first daycare day; spay/neuter + current vaccinations incl. Bordetella within "
           "6 months — bring records. Day camp M–F 6:30 AM–7 PM, ~$41/day, drop-in once "
           "passed. Enables the dog-free 🚐 BLD-N big ride while the van's at Geotrek."]
    res.update(range_name=f"A{empty+1}:F{empty+1}", values=[row],
               value_input_option="USER_ENTERED")
    print(f"Reservations: Camp Bow Wow interview row written at r{empty+1}.")

print("DONE.")
