"""
add_mammoth_menu.py — one-shot: add the MAMMOTH section to the DAY OPTIONS menu tab.

Mammoth (Aug 15–17) became flexible (MAM-A..D options live in rebuild_trip_tabs.py). This
adds the matching menu section (header bar + column header + 4 option rows + evenings) right
after the Crested Butte section and before the footer notes, mirroring the existing sections'
format. The MAM-A..D id cells get auto-linked to their option tabs by rebuild_trip_tabs.py
PHASE 3. Mammoth is Ian + Mochi only (Anny away); Mochi rule: ≤4 hr → A/C van, longer → daycare.

Idempotent: re-running overwrites the same block (guards on the header sentinel).
"""
import config
import gspread

gc = gspread.service_account(filename=config.CREDENTIALS_FILE)
sh = gc.open_by_key(config.SPREADSHEET_ID)
m = sh.worksheet("DAY OPTIONS")
sid = m.id

def rgb(r, g, b): return {"red": r/255, "green": g/255, "blue": b/255}
CRIMSON = rgb(127, 29, 29)      # Mammoth bar (matches the Dining Guide MAM color)
GREY    = rgb(224, 227, 235)    # column-header / sub-header
WHITE   = rgb(255, 255, 255)

HDR = ("MAMMOTH LAKES  —  DAY MENU   (Aug 15–17 · Ian + Mochi only, Anny away · "
       "base = Mammoth Lakes / van · Mochi rule: ≤4 hr = A/C van, longer = dog daycare · "
       "'Drive' = approx round trip · tap a Drive cell for the live route)")
COLHDR = ["Done", "ID", "Type", "Drive", "Day label (drops into the itinerary)",
          "Ian", "Anny", "Mochi", "Reservations / heads-up", "Weather backup"]
OPTS = [
    ["FALSE", "MAM-A", "Bike park", "~10 min",
     "Mammoth Bike Park — lift-served DH/enduro (Mochi to daycare)",
     "Mammoth Bike Park (Panorama Gondola, ~3,000 ft); Kamikaze + Off the Top", "—",
     "DAYCARE (no dogs at park, full day): PUP Hiking Co (760) 582-2176",
     "Bike-park ticket (Ikon = 2 free days); book PUP daycare 6–8 wks ahead",
     "Wet/closed → Lower Rock Creek (MAM-B) or town loops"],
    ["FALSE", "MAM-B", "Big ride", "~1h10",
     "Lower Rock Creek tech descent (Tom's Place) — Mochi to daycare",
     "Lower Rock Creek ~7.7 mi, ~1,900 ft descent; self-shuttle the road", "—",
     "DAYCARE (full day + drive): PUP Hiking / Sierra Dog Ventures (714) 609-8510",
     "Reserve daycare ahead; no trail fee",
     "Stay close: Sherwin + town loops (Mochi in A/C van <4 hr)"],
    ["FALSE", "MAM-C", "Dog day", "~30 min",
     "Acclimation + dog day: Convict Lake, Hot Creek, Lakes Basin",
     "Sherwin warm-up + Convict Lake loop + Hot Creek + Lakes Basin path", "—",
     "COMES ALL DAY — dog-friendly stops, A/C van for gaps (no daycare)",
     "None",
     "Hot/smoky → Lakes Basin shade + a town/rest day"],
    ["FALSE", "MAM-D", "Day trip", "~1h",
     "June Lake: a pedal-up ride + the June Lake Loop",
     "Reversed Peak (~2.8 mi tech) or June Mtn Chair 6 (~8 mi); PM lakes", "—",
     "MIXED: daycare if riding long, else A/C van; lakes dog-OK (June Lake Brewing = no dogs '26)",
     "Daycare if riding long",
     "Storms → scenic Loop drive + lakeshore walk"],
]
EVHDR = "MAMMOTH — EVENINGS (date-pinned)"
EVES = [
    ["Nightly", "", "Brewery patios — Mammoth Brewing Co. beer garden, Distant Brewing (dogs inside), Shelter Distilling"],
    ["After a ride", "", "Toomey's at the Village gondola base; Roberto's or Mammoth Tavern for dinner"],
    ["Splurge", "", "⭐ Skadi (Nordic tasting — reserve ahead) or Mammoth Rock Brasserie"],
    ["Up the Loop", "", "Tiger Bar / T-Bar (dog patio) in June Lake if you're there for MAM-D"],
]
NOTES = [
    "PHASE 1 = options that mirror the Itinerary. Next: add MORE options than days (extra choices + rain backups), then refine each to be ideal.",
    "MTB: Steamboat Bike Park + Evolution + Mammoth Bike Park are lift-served (NO dogs). Ride source of truth = the 'Activities — Hikes, Runs & MTB' tab (now incl. Eastern Sierra).",
    "Mammoth menu added 2026-06-01 — MAM-A..D above (Ian + Mochi only). Drive estimates approximate; tap a Drive cell for the live route time.",
]

# ── locate insertion: right after the last CB row, before the footer notes ───────
vals = m.get_all_values()
def row_of(pred):
    return next((i for i, r in enumerate(vals) if r and pred(r[0])), None)

# already added? (idempotent) — find existing Mammoth header
existing = row_of(lambda c: c.startswith("MAMMOTH LAKES  —  DAY MENU"))
notes_start = row_of(lambda c: c.startswith("PHASE 1 = options"))
if existing is not None:
    start = existing - 1  # the spacer row above the existing block
elif notes_start is not None:
    start = notes_start - 1  # overwrite from the blank row before the footer notes
else:
    start = len(vals) + 1

# ── assemble the block (0-indexed offsets from `start`) ──────────────────────────
PAD = lambda r: (r + [""] * (10 - len(r)))[:10]
block = []
block.append(PAD([""]))                 # spacer
block.append(PAD([HDR]))                # header bar
block.append(PAD(COLHDR))               # column header
for o in OPTS: block.append(PAD(o))     # 4 option rows
block.append(PAD([""]))                 # spacer
block.append(PAD([EVHDR]))              # evenings header
for e in EVES: block.append(PAD(e))     # evening rows
block.append(PAD([""]))                 # spacer
for n in NOTES: block.append(PAD([n]))  # footer notes (relocated below Mammoth)

# Clear any stale A:J horizontal merges across the block range first — a leftover merge
# from a previous menu layout silently swallows columns B–J of a values write (bit us
# 2026-06-01: the col-header + MAM-A rows inherited merges and lost everything past col A).
sh.batch_update({"requests": [{"unmergeCells": {"range": {"sheetId": sid,
    "startRowIndex": start, "endRowIndex": start + len(block),
    "startColumnIndex": 0, "endColumnIndex": 10}}}]})

m.update(range_name=f"A{start+1}", values=block, value_input_option="USER_ENTERED")

# row indices (0-based) of key rows within the written block
i_hdr   = start + 1
i_colhdr= start + 2
i_opt0  = start + 3
i_evhdr = start + 8
i_note0 = start + 14

def merge(r): return {"mergeCells": {"range": {"sheetId": sid, "startRowIndex": r, "endRowIndex": r+1,
    "startColumnIndex": 0, "endColumnIndex": 10}, "mergeType": "MERGE_ALL"}}
def fmt(r, bg, bold, white=False, size=None):
    tf = {"bold": bold}
    if white: tf["foregroundColor"] = WHITE
    if size: tf["fontSize"] = size
    return {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": r, "endRowIndex": r+1,
        "startColumnIndex": 0, "endColumnIndex": 10},
        "cell": {"userEnteredFormat": {"backgroundColor": bg, "textFormat": tf,
                 "horizontalAlignment": "LEFT", "wrapStrategy": "WRAP"}},
        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,wrapStrategy)"}}

reqs = [
    merge(i_hdr), fmt(i_hdr, CRIMSON, True, white=True, size=11),
    fmt(i_colhdr, GREY, True),
    merge(i_evhdr), fmt(i_evhdr, GREY, True),
]
for k in range(3):  # footer notes: merge across, plain
    reqs.append(merge(i_note0 + k))
# checkboxes on the four Done cells
reqs.append({"setDataValidation": {
    "range": {"sheetId": sid, "startRowIndex": i_opt0, "endRowIndex": i_opt0+4,
              "startColumnIndex": 0, "endColumnIndex": 1},
    "rule": {"condition": {"type": "BOOLEAN"}}}})
sh.batch_update({"requests": reqs})

print(f"Mammoth menu section written at rows {i_hdr+1}–{i_note0+3}. MAM-A..D will be "
      f"link-wired by rebuild_trip_tabs.py PHASE 3.")
