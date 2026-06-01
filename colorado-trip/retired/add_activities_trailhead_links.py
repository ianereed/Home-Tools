"""Add a Google Maps trailhead pin (native link) to each activity's Trailhead cell in
the 'Activities — Hikes, Runs & MTB' tab — same maps/search pattern as the MTB tabs.
The link is a live Google Maps search on '<trailhead>, <area>, <state>', so no
geocoding is needed (Google resolves it). Idempotent: re-running just re-sets the links.
"""
import urllib.parse
import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)
ws = sh.worksheet("Activities — Hikes, Runs & MTB")
sid = ws._properties["sheetId"]
LINKC = {"red": 21 / 255, "green": 101 / 255, "blue": 192 / 255}

STATE = {"Boulder": "CO", "Steamboat": "CO", "Crested Butte": "CO",
         "Lake Tahoe": "CA", "Mammoth Lakes": "CA"}

grid = ws.get_all_values()
th_col = area_col = None
reqs = []
linked = []
for i, row in enumerate(grid):
    # header rows define column positions for the section that follows
    if "Trailhead" in row:
        th_col = row.index("Trailhead")
        area_col = row.index("Area") if "Area" in row else 1
        continue
    if th_col is None:
        continue
    th = row[th_col].strip() if th_col < len(row) else ""
    area = row[area_col].strip() if area_col < len(row) else ""
    if not th or not area or area not in STATE:
        continue
    query = f"{th}, {area}, {STATE[area]}"
    url = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(query)
    reqs.append({"updateCells": {
        "rows": [{"values": [{
            "userEnteredValue": {"stringValue": th},
            "textFormatRuns": [{"startIndex": 0, "format": {
                "link": {"uri": url}, "underline": True, "foregroundColor": LINKC}}],
        }]}],
        "fields": "userEnteredValue,textFormatRuns",
        "start": {"sheetId": sid, "rowIndex": i, "columnIndex": th_col}}})
    linked.append((i + 1, th, area))

if reqs:
    sh.batch_update({"requests": reqs})
print(f"Linked {len(reqs)} trailhead cells:")
for r, th, area in linked:
    print(f"  R{r:>2} [{area}] {th}")
