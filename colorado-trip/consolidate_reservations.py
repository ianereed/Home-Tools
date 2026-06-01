"""
consolidate_reservations.py — make ONE clean in-sheet reservations tracker.

Today reservations live in TWO overlapping places:
  1. The "Todo — Todoist" tab — a complete, deadline-grouped task tracker (the good one).
  2. An "ADVANCE RESERVATIONS NEEDED" block embedded at the bottom of the Itinerary tab
     (older, partly redundant, Status column empty).

This script consolidates to a single source of truth:
  • renames "Todo — Todoist" -> "Reservations"
  • folds in the few items the Itinerary block had that the tracker lacked
    (Maroon Bells RFTA return bus + car-relocation; Fresno boarding backups -> Elaine's note;
     West Maroon use -> Dolly's note)
  • removes the Itinerary block and replaces it with a single pointer link to the tab

No Todoist sync program — the user reconciles sheet <-> Todoist by hand periodically.
Idempotent: safe to re-run (guards on sentinels). Read-only checks first, then writes.
"""

import config
import gspread

gc = gspread.service_account(filename=config.CREDENTIALS_FILE)
sh = gc.open_by_key(config.SPREADSHEET_ID)

LINKC = {"red": 21/255, "green": 101/255, "blue": 192/255}

# ── locate the tracker tab (new name or old) ─────────────────────────────────────
def _tracker():
    for name in ("Reservations", "Todo — Todoist"):
        try:
            return sh.worksheet(name)
        except gspread.exceptions.WorksheetNotFound:
            continue
    raise SystemExit("No tracker tab found (looked for 'Reservations' / 'Todo — Todoist').")

trk = _tracker()
vals = trk.get_all_values()
flat = "\n".join("\t".join(r) for r in vals)

# ── 1. append the two genuinely-missing bookings (idempotent) ────────────────────
# columns: A=checkbox  B=task  C=category  D=due-by  E=contact/site  F=notes
NEW_ROWS = [
    ["FALSE",
     "WEST MAROON PASS point-to-point (Aug 10 or 11) — book all three; full logistics on the CB-C tab",
     "", "", "", ""],
    ["FALSE",
     "Book Maroon Bells RFTA bus — 1-way return ticket (West Maroon hike) !!2 Jun 1",
     "Activities / Ian", "Jun 1", "visitmaroonbells.com",
     "$10/hiker. Maroon Lake → Aspen Highlands (15 min); last bus down 5:00 PM. The return leg "
     "of the West Maroon point-to-point. Confirm leashed-dog policy. (Dolly's shuttle to the "
     "West Maroon TH is the 3rd booking — see the Dolly's row above.)"],
    ["FALSE",
     "Arrange Maroon Bells Shuttles — car relocation CB→Aspen (West Maroon hike) !!2 Jun 1",
     "Activities / Ian", "Jun 1", "maroonbellsshuttles.com",
     "Drives your car CB→Aspen while you hike West Maroon so it's waiting at the end. Request a "
     "quote; coordinate the Aspen drop with where the RFTA bus leaves you (Aspen Highlands)."],
]

def _first_blank_row(values):
    for i, r in enumerate(values):
        if not any(c.strip() for c in r):
            return i
    return len(values)

if "Maroon Bells RFTA bus" not in flat:
    start = _first_blank_row(vals)
    trk.update(range_name=f"A{start+1}", values=NEW_ROWS, value_input_option="USER_ENTERED")
    # give the new checkbox cells the same boolean toggle as the rest of column A
    sh.batch_update({"requests": [{"setDataValidation": {
        "range": {"sheetId": trk.id, "startRowIndex": start, "endRowIndex": start+len(NEW_ROWS),
                  "startColumnIndex": 0, "endColumnIndex": 1},
        "rule": {"condition": {"type": "BOOLEAN"}}}}]})
    print(f"Appended {len(NEW_ROWS)} West-Maroon rows at row {start+1}.")
else:
    print("West-Maroon rows already present — skipping append.")

# ── 2. enrich two existing notes (idempotent) ────────────────────────────────────
def _patch_note(match_in_task, sentinel, append_text):
    """Append `append_text` to the Notes (col F) of the row whose task (col B) contains
    match_in_task, unless `sentinel` is already in that note."""
    cur = trk.get_all_values()
    for i, r in enumerate(cur):
        if len(r) > 1 and match_in_task in r[1]:
            note = r[5] if len(r) > 5 else ""
            if sentinel in note:
                print(f"  note for '{match_in_task[:30]}…' already enriched — skip.")
                return
            trk.update(range_name=f"F{i+1}", values=[[note.rstrip() + "  " + append_text]],
                       value_input_option="USER_ENTERED")
            print(f"  enriched note for '{match_in_task[:30]}…'.")
            return
    print(f"  ⚠️  no row matched '{match_in_task}'.")

_patch_note("Elaine's Pet Resorts", "Backups:",
            "Backups if full: Pet Medical Center & Spa, Fresno (621 W Fallbrook Ave, 4.2–4.5★, "
            "557+ rev, vet on-site + DogTV) and Visalia's VIP Pet Boarding (438 S Goddard St, "
            "(559) 732-4803, 4.7★, open 365, closer to the Kings Canyon trailhead).")
_patch_note("Dolly's Mountain Shuttle", "West Maroon",
            "Also the ride to the West Maroon Pass TH (Aug 10/11): $55/seat × 5 = $275, CB→TH "
            "~40 min, cancel 48 hr. (Phone differs across tabs — verify the current number.)")

# ── 3. rename tracker -> "Reservations" ──────────────────────────────────────────
if trk.title != "Reservations":
    sh.batch_update({"requests": [{"updateSheetProperties": {
        "properties": {"sheetId": trk.id, "title": "Reservations"}, "fields": "title"}}]})
    print('Renamed tracker tab -> "Reservations".')
else:
    print('Tracker already named "Reservations".')
res_gid = trk.id

# ── 4. remove the Itinerary "Advance Reservations" block + leave a pointer ────────
itin = sh.worksheet("Itinerary")
ivals = itin.get_all_values()
hdr = next((i for i, r in enumerate(ivals) if r and "ADVANCE RESERVATIONS" in r[0].upper()), None)
if hdr is None:
    print("Itinerary block already removed — pointer assumed in place.")
else:
    res_url = f"https://docs.google.com/spreadsheets/d/{config.SPREADSHEET_ID}/edit#gid={res_gid}"
    pointer = "📋 Advance reservations & booking checklist → open the “Reservations” tab"
    # delete the block BELOW the header row (header row becomes the pointer)
    end = len(ivals)
    sh.batch_update({"requests": [
        {"deleteDimension": {"range": {"sheetId": itin.id, "dimension": "ROWS",
                                       "startIndex": hdr+1, "endIndex": end}}},
        {"updateCells": {
            "rows": [{"values": [{"userEnteredValue": {"stringValue": pointer},
                                  "textFormatRuns": [{"startIndex": 0, "format": {
                                      "link": {"uri": res_url}, "underline": True,
                                      "foregroundColor": LINKC}}]}]}],
            "fields": "userEnteredValue,textFormatRuns",
            "start": {"sheetId": itin.id, "rowIndex": hdr, "columnIndex": 0}}},
        {"updateCells": {
            "rows": [{"values": [{"userEnteredFormat": {"textFormat": {"bold": True}}}]}],
            "fields": "userEnteredFormat.textFormat.bold",
            "start": {"sheetId": itin.id, "rowIndex": hdr, "columnIndex": 0}}},
    ]})
    print(f"Removed Itinerary reservations block (rows {hdr+2}–{end}); wrote pointer at row {hdr+1}.")

print("DONE — single Reservations tab; Itinerary points to it.")
