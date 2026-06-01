"""Stamp the West Maroon Pass hike option onto the Itinerary tab:
  - Aug 10 & Aug 11 'Everyone Together Plan' column gets the option + a tab pointer.
  - Three new bookings inserted into the ADVANCE RESERVATIONS section.
Re-runnable-ish: skips the cell writes if the option text is already present, and
skips the reservation insert if Dolly's is already listed.
"""
import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)
ws = sh.worksheet('Itinerary')
sid = ws._properties['sheetId']

OPT_COMMON = ("⭐ OPTION (full day): Crested Butte → Aspen via West Maroon Pass — "
              "4 people + Mochi, hike one-way with car relocation + 2 shuttles. "
              "See the CB-C option tab.")  # logistics folded into CB-C (West Maroon Pass tab retired)
OPT_10 = OPT_COMMON + " Note: conflicts with tonight's Alpenglow Concert."
OPT_11 = OPT_COMMON + " Note: replaces bike-park day; pushes packing to Aug 12 AM."

vals = ws.get_all_values()

def col_letter(idx0):  # 0-based -> A1 letter
    return chr(ord('A') + idx0)

# ── cell writes (Everyone Together = col O idx14 ; More Info = col Q idx16) ─────
cell_updates = []
if "West Maroon Pass" not in vals[47][14]:        # Aug 10 = row 48
    cell_updates.append(("O48", OPT_10))
    mi = vals[47][16]
    cell_updates.append(("Q48", (mi + " | " if mi else "") + "→ CB-C option tab"))
if "West Maroon Pass" not in vals[48][14]:        # Aug 11 = row 49
    cell_updates.append(("O49", OPT_11))
    cell_updates.append(("Q49", "→ CB-C option tab"))

for a1, text in cell_updates:
    ws.update([[text]], a1, value_input_option="USER_ENTERED")
    print(f"  set {a1}")

# highlight the two option cells (amber, bold)
def rgb(r, g, b):
    return {"red": r/255, "green": g/255, "blue": b/255}
reqs = []
for r0 in (47, 48):  # rows 48,49
    reqs.append({"repeatCell": {
        "range": {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r0+1,
                  "startColumnIndex": 14, "endColumnIndex": 15},
        "cell": {"userEnteredFormat": {
            "backgroundColor": rgb(255, 243, 205),
            "textFormat": {"bold": True, "foregroundColor": rgb(120, 70, 0)},
            "wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
        "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment)"}})

# ── insert the 3 bookings after the last clean reservation row (Donna = row 87) ─
already = any("Dolly" in (row[0] if row else "") for row in vals)
if not already:
    INSERT_AT = 88  # 1-based; before the 'Rank' dog-daycare table at row 88
    new_rows = [
        ["West Maroon Pass hike (Aug 10 or 11) — book all three", "", "", "",
         "Point-to-point CB→Aspen over West Maroon Pass. Full logistics on the CB-C option tab."],
        ["Dolly's Mountain Shuttle — ride to West Maroon TH", "Early — books up, esp. weekends",
         "crestedbutteshuttle.com", "",
         "$55/seat × 5 (4 people + Mochi needs its own seat) = $275 ($220 min). CB → West Maroon Trailhead, ~40 min. 970-209-1568. Cancel 48 hr."],
        ["Maroon Bells RFTA bus — 'One-Way Return Only' ticket", "2026 shuttle res open now",
         "visitmaroonbells.com", "",
         "$10/hiker. Maroon Lake → Aspen Highlands (15 min). Last bus down 5:00 PM. Confirm leashed-dog policy."],
        ["Maroon Bells Shuttles — car relocation CB→Aspen", "Well in advance",
         "maroonbellsshuttles.com", "",
         "Drives your car CB→Aspen while you hike so it's waiting. Request quote. Coordinate the Aspen drop point with where the bus leaves you (Aspen Highlands)."],
    ]
    ws.insert_rows(new_rows, row=INSERT_AT, value_input_option="USER_ENTERED")
    print(f"  inserted {len(new_rows)} reservation rows at row {INSERT_AT}")
    # bold the subheader row (now at INSERT_AT, 0-based INSERT_AT-1)
    sub0 = INSERT_AT - 1
    reqs.append({"repeatCell": {
        "range": {"sheetId": sid, "startRowIndex": sub0, "endRowIndex": sub0+1,
                  "startColumnIndex": 0, "endColumnIndex": 5},
        "cell": {"userEnteredFormat": {
            "backgroundColor": rgb(255, 243, 205),
            "textFormat": {"bold": True, "foregroundColor": rgb(120, 70, 0)}}},
        "fields": "userEnteredFormat(backgroundColor,textFormat)"}})
else:
    print("  reservations already present — skipped insert")

if reqs:
    sh.batch_update({"requests": reqs})
print("OK: Itinerary updated.")
